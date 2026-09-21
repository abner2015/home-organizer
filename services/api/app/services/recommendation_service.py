"""Recommendation service — runs the 9-step pipeline + persists the result.

This sits between the API endpoint and:

- :class:`app.agents.RecommendationAgent` — the pure state machine.
- :mod:`app.models` — the persistence primitives.

Responsibilities:

1. Build a fresh ``ToolRegistry`` bound to the request's ``AsyncSession``.
2. Run the agent to completion (it's a pure function — doesn't write rows).
3. Persist the resulting ``AgentTrace`` and ``Recommendation`` rows.
4. Surface a view-shaped dict for the API to wrap in a Pydantic schema.

The agent itself does not write any DB rows; persistence lives here so the
agent stays testable without a database.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.pipeline import AgentRunResult, RecommendationAgent
from app.agents.state import RecommendationState
from app.ai.provider import AIProvider
from app.core.exceptions import NotFoundError
from app.db.enums import RecommendationStatus
from app.models import AgentTrace, Item, Recommendation
from app.tools import get_default_registry
from app.tools.home_tools import get_storage_slots

# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class RecommendOutcome:
    """Public-shaped recommendation outcome.

    ``chosen_slot_id`` is ``None`` if the agent ended in FAILED. ``candidates``
    is the Top-3 from the agent's ranked list (post-filter, with det_score).
    """

    recommendation: Recommendation
    result: AgentRunResult

    @property
    def ok(self) -> bool:
        return self.result.ok

    def to_dict(self) -> dict[str, Any]:
        return _to_response_dict(self.recommendation, self.result)


# --------------------------------------------------------------------------- helpers


async def _ensure_item_in_home(
    db: AsyncSession, item_id: uuid.UUID, home_id: uuid.UUID
) -> Item:
    item = (
        await db.execute(select(Item).where(Item.id == item_id))
    ).scalar_one_or_none()
    if item is None or item.home_id != home_id:
        raise NotFoundError("Item not found")
    return item


def candidate_view_from_slot(
    slot: dict[str, Any], *, is_recommended: bool = False
) -> dict[str, Any]:
    """Project one slot dict into the ``CandidateView`` shape.

    Shared by the recommendation endpoints (whose candidates carry a
    ``det_score`` / ``confidence`` / ``reason`` from the pipeline) and
    ``GET /items/{id}/candidates`` (whose candidates are the raw ranker
    output).
    """
    sid = slot.get("id") or slot.get("slot_id")
    return {
        "slot_id": str(sid) if sid else str(uuid.uuid4()),
        "code": slot.get("code") or "",
        "label": slot.get("label") or "",
        "full_path": slot.get("full_path") or "",
        "room_name": slot.get("room_name") or "",
        "unit_name": slot.get("unit_name") or "",
        "section_name": slot.get("section_name") or "",
        "score": int(slot.get("det_score") or 0),
        "confidence": float(slot.get("confidence") or 0.0),
        "reason": slot.get("reason") or "",
        "matched_rules": [str(x) for x in (slot.get("matched_rules") or [])],
        "evidence_item_ids": [str(x) for x in (slot.get("evidence_item_ids") or [])],
        "is_recommended": is_recommended,
    }


def _candidate_to_view(c: dict[str, Any], *, is_recommended: bool = False) -> dict[str, Any]:
    """Project one ranked-candidate dict into the API view shape."""
    return candidate_view_from_slot(c, is_recommended=is_recommended)


def _top3(result: AgentRunResult) -> list[dict[str, Any]]:
    """Pick the top-3 candidates to return to the UI. When the LLM succeeded,
    the chosen one is surfaced first, followed by the next 2 from the ranked
    list. When failed, just the pre-decide ranked top-3.
    """
    ranked = list(result.candidates or [])
    if not ranked:
        return []
    chosen_uuid = result.chosen_slot_id if result.ok else None
    # The agent records the LLM's chosen-slot reason on the DECIDE step's
    # payload. Pluck it out so the chosen candidate's view row carries the
    # same human-readable justification the user saw during accept.
    llm_reason = _extract_decide_reason(result)
    if chosen_uuid is not None:
        chosen_str = str(chosen_uuid)
        front = [c for c in ranked if str(c.get("id")) == chosen_str]
        rest = [c for c in ranked if str(c.get("id")) != chosen_str]
        ordered = front + rest
    else:
        ordered = list(ranked)
    out = []
    for c in ordered[:3]:
        is_chosen = chosen_uuid is not None and str(c.get("id")) == str(chosen_uuid)
        view = _candidate_to_view(c, is_recommended=is_chosen)
        if (
            llm_reason
            and is_chosen
            and not view.get("reason")
        ):
            view["reason"] = llm_reason
        out.append(view)
    return out


def _extract_decide_reason(result: AgentRunResult) -> str:
    """Pull the LLM-chosen slot's reason from the last DECIDE step's payload."""
    for step in reversed(result.steps):
        if step.state == RecommendationState.DECIDE:
            payload = step.payload or {}
            reason = payload.get("reason")
            if isinstance(reason, str) and reason:
                return reason
            return ""
    return ""


def _to_response_dict(
    rec: Recommendation, result: AgentRunResult
) -> dict[str, Any]:
    return {
        "recommendation_id": str(rec.id),
        "item_id": str(rec.item_id),
        "trace_id": str(rec.agent_trace_id),
        "chosen_slot_id": (
            str(rec.chosen_slot_id) if rec.chosen_slot_id else None
        ),
        "status": rec.status,
        "state": (
            RecommendationState.ANSWER.value
            if result.state == RecommendationState.ANSWER
            else RecommendationState.FAILED.value
        ),
        "retries_used": result.retries_used,
        "pre_filter_count": result.pre_filter_count,
        "post_filter_count": result.post_filter_count,
        "candidates": _top3(result),
        "error": (
            result.error
            if result.state == RecommendationState.FAILED
            else None
        ),
    }


def _final_status(result: AgentRunResult) -> str:
    if result.ok:
        return "success"
    if result.steps and result.error:
        return "verifier_failed"
    return "error"


def _duration_ms(result: AgentRunResult) -> int:
    if not result.steps:
        return 0
    start = min(s.started_at for s in result.steps)
    end = max(s.ended_at for s in result.steps)
    return max(0, int((end - start).total_seconds() * 1000))


def _steps_json(result: AgentRunResult) -> list[dict[str, Any]]:
    return [s.to_json() for s in result.steps]


def _build_recommendation_candidates(
    result: AgentRunResult,
) -> list[dict[str, Any]]:
    """Project the agent's top-3 ranked candidates into the persisted shape.

    We deliberately keep the agent's `det_score` on the JSONB blob so
    downstream UI code can sort / filter without re-running the ranker.
    """
    llm_reason = _extract_decide_reason(result)
    chosen_uuid = result.chosen_slot_id if result.ok else None
    out: list[dict[str, Any]] = []
    for c in _top3_full(result):
        sid = c.get("id") or c.get("slot_id")
        sid_str = str(sid) if sid else str(uuid.uuid4())
        reason = c.get("reason") or ""
        if not reason and llm_reason and sid_str == str(chosen_uuid):
            reason = llm_reason
        out.append(
            {
                "slot_id": sid_str,
                "confidence": float(c.get("confidence") or 0.0),
                "reason": reason,
                "matched_rules": list(c.get("matched_rules") or []),
                "evidence_item_ids": [
                    str(x) for x in (c.get("evidence_item_ids") or [])
                ],
                "code": c.get("code") or "",
                "label": c.get("label") or "",
                "full_path": c.get("full_path") or "",
                "room_name": c.get("room_name") or "",
                "unit_name": c.get("unit_name") or "",
                "section_name": c.get("section_name") or "",
                "det_score": int(c.get("det_score") or 0),
            }
        )
    return out


def _top3_full(result: AgentRunResult) -> list[dict[str, Any]]:
    """Top-3 (full slot dicts, no view projection) for persistence."""
    ranked = list(result.candidates or [])
    if not ranked:
        return []
    chosen_uuid = result.chosen_slot_id if result.ok else None
    if chosen_uuid is not None:
        chosen_str = str(chosen_uuid)
        front = [c for c in ranked if str(c.get("id")) == chosen_str]
        rest = [c for c in ranked if str(c.get("id")) != chosen_str]
        ordered = front + rest
    else:
        ordered = list(ranked)
    return ordered[:3]


# --------------------------------------------------------------------------- service


async def run_recommendation(
    db: AsyncSession,
    *,
    provider: AIProvider,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    max_retries: int = 2,
) -> RecommendOutcome:
    """Run the 9-step pipeline and persist the AgentTrace + Recommendation rows.

    Always returns a :class:`RecommendOutcome`. When the pipeline FAILED the
    outcome's ``ok`` is False and ``chosen_slot_id`` is None — the
    Recommendation row is still persisted with ``status='pending'`` and
    ``chosen_slot_id=NULL`` so the user can re-trigger later.

    Raises:
        NotFoundError: item doesn't exist or belongs to a different home.
    """
    await _ensure_item_in_home(db, item_id, home_id)

    tools = get_default_registry(db)
    agent = RecommendationAgent(
        ai=provider, tools=tools, db=db, max_retries=max_retries
    )
    result: AgentRunResult = await agent.run(
        home_id=home_id, user_id=user_id, item_id=item_id
    )

    # Persist AgentTrace (1 row per run).
    trace = AgentTrace(
        home_id=home_id,
        user_id=user_id,
        item_id=item_id,
        steps=_steps_json(result),
        final_status=_final_status(result),
        total_duration_ms=_duration_ms(result),
        error=result.error,
    )
    db.add(trace)
    await db.flush()

    # Persist Recommendation row.
    rec = Recommendation(
        item_id=item_id,
        agent_trace_id=trace.id,
        candidates=_build_recommendation_candidates(result),
        pre_filter_count=result.pre_filter_count,
        post_filter_count=result.post_filter_count,
        chosen_slot_id=result.chosen_slot_id,
        status=RecommendationStatus.PENDING.value,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    await db.refresh(trace)
    return RecommendOutcome(recommendation=rec, result=result)


# ------------------------------------------------------------------- read-back


def _merge_live_slot(
    candidate: dict[str, Any], live_slots: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Overlay a persisted candidate with the slot's *current* display fields.

    Snapshot values (``confidence`` / ``reason`` / ``det_score``) win; only
    location metadata is refreshed, because a slot can be renamed after the
    recommendation ran.
    """
    live = live_slots.get(str(candidate.get("slot_id")))
    if not live:
        return candidate
    merged = dict(candidate)
    for key in ("code", "label", "full_path", "room_name", "unit_name", "section_name"):
        if live.get(key):
            merged[key] = live[key]
    return merged


async def get_recommendation_view(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    recommendation_id: uuid.UUID,
) -> dict[str, Any]:
    """Load a persisted recommendation and project it into the
    ``RecommendResponse`` shape.

    ``retries_used`` is not a column on ``recommendations`` — it is recovered
    from the AgentTrace's RETRY steps, and the failure message from the
    trace's ``error`` column.

    Raises:
        NotFoundError: unknown id, or the recommendation's item belongs to
            another home (404 rather than 403, so existence never leaks).
    """
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.id == recommendation_id)
        )
    ).scalar_one_or_none()
    if rec is None:
        raise NotFoundError("Recommendation not found")
    item = (
        await db.execute(select(Item).where(Item.id == rec.item_id))
    ).scalar_one_or_none()
    if item is None or item.home_id != home_id:
        raise NotFoundError("Recommendation not found")

    trace = (
        await db.execute(select(AgentTrace).where(AgentTrace.id == rec.agent_trace_id))
    ).scalar_one_or_none()
    live_slots = {s["id"]: s for s in await get_storage_slots(db=db, home_id=home_id)}
    chosen = str(rec.chosen_slot_id) if rec.chosen_slot_id else None
    candidates = [
        candidate_view_from_slot(
            _merge_live_slot(c, live_slots), is_recommended=str(c.get("slot_id")) == chosen
        )
        for c in (rec.candidates or [])
        if isinstance(c, dict)
    ]
    steps = (trace.steps if trace else None) or []
    retries_used = sum(
        1
        for step in steps
        if isinstance(step, dict) and step.get("state") == RecommendationState.RETRY.value
    )
    return {
        "recommendation_id": str(rec.id),
        "item_id": str(rec.item_id),
        "trace_id": str(rec.agent_trace_id),
        "chosen_slot_id": chosen,
        "status": rec.status,
        "state": (
            RecommendationState.ANSWER.value
            if chosen
            else RecommendationState.FAILED.value
        ),
        "retries_used": retries_used,
        "pre_filter_count": rec.pre_filter_count,
        "post_filter_count": rec.post_filter_count,
        "candidates": candidates,
        "error": (
            None
            if chosen
            else ((trace.error if trace else None) or "无符合硬规则的位置")
        ),
    }


__all__ = [
    "RecommendOutcome",
    "candidate_view_from_slot",
    "get_recommendation_view",
    "run_recommendation",
]
