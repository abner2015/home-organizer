"""The 9-step recommendation pipeline (see docs/AGENT.md).

The pipeline is an explicit state machine:

    INTAKE → UNDERSTAND → RETRIEVE → CANDIDATE_GENERATION → FILTER
      → RANK → DECIDE → VERIFY → (RETRY → DECIDE …) → ANSWER
      └──────────────────────────────────────────────→ FAILED

Steps 1–6 are pure (no LLM). Step 7 (DECIDE) calls the LLM via
``AIProvider.rank_candidates``. Step 8 (VERIFY) runs the 9 check functions.
On failure we loop back to DECIDE with ``last_failure`` set; after
``max_retries`` exhausted (default 2) the pipeline transitions to FAILED.

The orchestrator records one :class:`AgentStepResult` per transition in
``steps`` (the same list later persisted to AgentTrace.steps by the
``verify_recommendation`` write tool).
"""
from __future__ import annotations

import asyncio
import json
import uuid as uuid_mod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.candidate_gen import generate_candidates, hard_filter
from app.agents.context import AgentContext
from app.agents.ranking import rank_slots
from app.agents.reason import build_reason, is_acceptable_llm_reason
from app.agents.state import AgentStepResult, RecommendationState
from app.ai.observability import hash_prompt
from app.ai.provider import AIProvider, RankingOutput
from app.tools.recommendation_tools import get_rejected_slot_ids
from app.tools.registry import ToolRegistry
from app.verification.context import VerificationContext
from app.verification.verifier import run_verifier

MAX_RETRIES_DEFAULT = 2


def _fingerprint_rank_call(
    *,
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    last_failure: str | None,
) -> str:
    """Stable SHA-256 fingerprint of the DECIDE step's inputs.

    The provider builds the rank prompt internally (docs/AI.md §2 — the
    Protocol's ``rank_candidates`` has no prompt parameter). To produce a
    correlation key without changing the Protocol, we hash a canonical JSON
    dump of the inputs. Same inputs (after the deterministic ranker has
    produced the same candidate list) ⇒ same hash. (P2.1)

    Candidate slots are reduced to ``(id, det_score, score_terms)`` — those
    are the only inputs that flow into the prompt — and ``item`` is reduced
    to its identifying fields.
    """
    fingerprint = {
        "item": {
            "name": item.get("name"),
            "category": item.get("category"),
            "subcategory": item.get("subcategory"),
            "estimated_size": item.get("estimated_size"),
            "is_sensitive": bool(item.get("is_sensitive", False)),
            "needs_lock": bool(item.get("needs_lock", False)),
        },
        "candidates": [
            {
                "id": str(c.get("id")),
                "det_score": c.get("det_score"),
                "score_terms": c.get("score_terms") or {},
            }
            for c in candidates
        ],
        "rule_names": sorted(str(r.get("name")) for r in rules),
        "preference_count": len(preferences),
        "history_count": len(history),
        "last_failure": last_failure,
    }
    canonical = json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, default=str)
    return hash_prompt(canonical)


@dataclass(slots=True)
class AgentRunResult:
    """Final result returned by :meth:`RecommendationAgent.run`."""

    state: RecommendationState
    chosen_slot_id: uuid_mod.UUID | None
    # Ranked candidates (post-filter, descending ``det_score``) — the list the
    # LLM was shown, truncated to the ranker's limit. Callers that surface a
    # "top 3" rely on this being in rank order, not DB row order.
    candidates: list[dict[str, Any]]
    retries_used: int
    steps: list[AgentStepResult] = field(default_factory=list)
    error: str | None = None
    # Observability fields — propagated to AgentTrace.steps and surfaced in
    # the eval harness so failed runs are diagnosable without re-running.
    # (P2.1) ``pre_filter_count`` / ``post_filter_count`` are also available
    # via ``_step_rank`` payloads; the fields below let callers read them
    # without walking the step list.
    pre_filter_count: int = 0
    post_filter_count: int = 0
    # Per-candidate ``score_terms`` (ranker output), in rank order. Empty when
    # the candidate set is empty.
    score_breakdown: list[dict[str, int]] = field(default_factory=list)
    # SHA-256 fingerprint of the DECIDE call's inputs (item + ranked candidates
    # + rules + preferences + history). Same inputs → same hash → easy to
    # correlate runs that produced the same LLM call. ``None`` when DECIDE
    # never ran (e.g. candidate set empty).
    prompt_hash: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == RecommendationState.ANSWER and self.chosen_slot_id is not None


class RecommendationAgent:
    """The full pipeline. Stateless except for the injected dependencies."""

    def __init__(
        self,
        *,
        ai: AIProvider,
        tools: ToolRegistry,
        db: AsyncSession,
        max_retries: int = MAX_RETRIES_DEFAULT,
    ) -> None:
        self.ai = ai
        self.tools = tools
        self.db = db
        self.max_retries = max_retries

    # ------------------------------------------------------------------ entry

    async def run(
        self,
        *,
        home_id: uuid_mod.UUID,
        user_id: uuid_mod.UUID,
        item_id: uuid_mod.UUID,
    ) -> AgentRunResult:
        """Run the full pipeline. The caller persists the result."""
        ctx = AgentContext(
            home_id=home_id,
            user_id=user_id,
            item_id=item_id,
            tools=self.tools,
            max_retries=self.max_retries,
        )
        steps: list[AgentStepResult] = []

        # 1) INTAKE
        steps.append(await self._step_intake(ctx))

        # 2) UNDERSTAND
        steps.append(await self._step_understand(ctx))

        # 3) RETRIEVE — collect everything the downstream steps need.
        steps.append(await self._step_retrieve(ctx))

        # 4+5) Candidate Generation + Hard Filter — fail fast if empty.
        gen_step = await self._step_generate(ctx)
        steps.append(gen_step)
        filt_step = await self._step_filter(ctx)
        steps.append(filt_step)
        if not ctx.candidates:
            # Distinguish "nothing fits" from "you vetoed everything" — the
            # latter tells the user the way out is to stop rejecting, not to
            # add storage.
            error = (
                "该物品的候选位置均已被你排除"
                if ctx.excluded_slot_ids
                else "无符合硬规则的位置"
            )
            return AgentRunResult(
                state=RecommendationState.FAILED,
                chosen_slot_id=None,
                candidates=[],
                retries_used=0,
                steps=steps,
                error=error,
                pre_filter_count=ctx.pre_filter_count,
                post_filter_count=ctx.post_filter_count,
            )

        # 6) RANK (deterministic)
        rank_step = await self._step_rank(ctx)
        steps.append(rank_step)

        # 7+8) DECIDE + VERIFY (with retries)
        while True:
            decide_step = await self._step_decide(ctx)
            steps.append(decide_step)

            verify_step = await self._step_verify(ctx)
            steps.append(verify_step)

            if verify_step.payload.get("ok"):
                return AgentRunResult(
                    state=RecommendationState.ANSWER,
                    chosen_slot_id=ctx.chosen_slot_id,
                    candidates=ctx.ranked_candidates,
                    retries_used=ctx.retries_used,
                    steps=steps,
                    pre_filter_count=ctx.pre_filter_count,
                    post_filter_count=ctx.post_filter_count,
                    score_breakdown=list(rank_step.payload.get("score_breakdown") or []),
                    prompt_hash=decide_step.payload.get("prompt_hash"),
                )

            if ctx.retries_used >= ctx.max_retries:
                return AgentRunResult(
                    state=RecommendationState.FAILED,
                    chosen_slot_id=None,
                    candidates=ctx.ranked_candidates,
                    retries_used=ctx.retries_used,
                    steps=steps,
                    error=ctx.last_failure or "verification failed",
                    pre_filter_count=ctx.pre_filter_count,
                    post_filter_count=ctx.post_filter_count,
                    score_breakdown=list(rank_step.payload.get("score_breakdown") or []),
                    prompt_hash=decide_step.payload.get("prompt_hash"),
                )

            ctx.retries_used += 1
            retry_step = await self._step_retry(ctx)
            steps.append(retry_step)

    # --------------------------------------------------------------- steps

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    async def _step_intake(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = (await ctx.tools.get_items(home_id=ctx.home_id, item_id=ctx.item_id))[0]
        ctx.item = item
        return AgentStepResult(
            state=RecommendationState.INTAKE,
            started_at=start,
            ended_at=self._now(),
            payload={"item_id": str(ctx.item_id)},
        )

    async def _step_understand(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = ctx.item or {}
        return AgentStepResult(
            state=RecommendationState.UNDERSTAND,
            started_at=start,
            ended_at=self._now(),
            payload={
                "name": item.get("name"),
                "category": item.get("category"),
                "is_sensitive": item.get("is_sensitive"),
            },
        )

    async def _step_retrieve(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        home, rooms, slots, prefs, rules, history = await asyncio.gather(
            ctx.tools.get_home(home_id=ctx.home_id),
            ctx.tools.get_rooms(home_id=ctx.home_id),
            ctx.tools.get_storage_slots(home_id=ctx.home_id),
            ctx.tools.get_user_preferences(home_id=ctx.home_id),
            ctx.tools.get_home_rules(home_id=ctx.home_id),
            ctx.tools.get_item_placements(home_id=ctx.home_id, item_id=ctx.item_id),
        )
        excluded = await get_rejected_slot_ids(
            db=self.db, home_id=ctx.home_id, item_id=ctx.item_id
        )
        ctx.home = home
        ctx.rooms = rooms
        ctx.raw_slots = slots
        ctx.preferences = prefs
        ctx.rules = rules
        ctx.history = history
        ctx.excluded_slot_ids = excluded
        return AgentStepResult(
            state=RecommendationState.RETRIEVE,
            started_at=start,
            ended_at=self._now(),
            payload={
                "home_id": str(ctx.home_id),
                "rooms": len(rooms),
                "slots": len(slots),
                "rules": len(rules),
                "preferences": len(prefs),
                "history": len(history),
                "excluded": len(excluded),
            },
        )

    async def _step_generate(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = ctx.item or {}
        candidates = generate_candidates(ctx.raw_slots, item)
        ctx.pre_filter_count = len(candidates)
        return AgentStepResult(
            state=RecommendationState.CANDIDATE_GENERATION,
            started_at=start,
            ended_at=self._now(),
            payload={"count": len(candidates)},
        )

    async def _step_filter(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = ctx.item or {}
        raw_candidates = [
            c
            for c in generate_candidates(ctx.raw_slots, item)
            if uuid_mod.UUID(str(c["id"])) not in ctx.excluded_slot_ids
        ]
        hard_rules = [r for r in ctx.rules if r.get("rule_type") == "hard"]
        active_count = {
            uuid_mod.UUID(s["id"]): int(s.get("active_count", 0))
            for s in raw_candidates
        }
        filtered = hard_filter(
            raw_candidates,
            item=item,
            hard_rules=hard_rules,
            active_count=active_count,
        )
        ctx.candidates = filtered
        ctx.post_filter_count = len(filtered)
        return AgentStepResult(
            state=RecommendationState.FILTER,
            started_at=start,
            ended_at=self._now(),
            payload={"count": len(filtered), "excluded_count": len(ctx.excluded_slot_ids)},
        )

    async def _step_rank(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = ctx.item or {}
        soft_rules = [r for r in ctx.rules if r.get("rule_type") == "soft"]
        ranked = rank_slots(
            ctx.candidates,
            item=item,
            preferences=ctx.preferences,
            history=ctx.history,
            soft_rules=soft_rules,
            actor_user_id=ctx.user_id,
            limit=20,
        )
        ctx.ranked_candidates = ranked
        # ``score_terms`` is the ranker's per-candidate weighted breakdown (7
        # keys: category/room/path/capacity/preference/history/soft_rule).
        # Surface it on the step payload so the AgentTrace (and the eval
        # runner) can show why each candidate ranked where it did — without
        # re-running the ranker. (P2.1)
        score_breakdown = [c.get("score_terms") or {} for c in ranked]
        return AgentStepResult(
            state=RecommendationState.RANK,
            started_at=start,
            ended_at=self._now(),
            payload={
                "count": len(ranked),
                "top_score": ranked[0]["det_score"] if ranked else 0,
                "score_breakdown": score_breakdown,
            },
        )

    async def _step_decide(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        item = ctx.item or {}
        # The provider builds the Rank prompt itself (docs/AI.md §2 — the
        # Protocol's rank_candidates takes no prompt parameter), so we hash a
        # stable fingerprint of the inputs as a correlation key. Same inputs
        # → same hash, so traces with identical LLM calls group together.
        # (P2.1)
        prompt_hash = _fingerprint_rank_call(
            item=item,
            candidates=ctx.ranked_candidates,
            rules=ctx.rules,
            preferences=ctx.preferences,
            history=ctx.history,
            last_failure=ctx.last_failure,
        )
        chosen: dict[str, Any] | None = None
        try:
            output: RankingOutput = await self.ai.rank_candidates(
                item=item,
                candidates=ctx.ranked_candidates,
                rules=ctx.rules,
                preferences=ctx.preferences,
                history=ctx.history,
                last_failure=ctx.last_failure,
            )
            if output.candidates:
                top_pick = max(output.candidates, key=lambda c: c.confidence)
                ctx.chosen_slot_id = top_pick.slot_id
                # Keep every *gated* reason, not just the winner's — the other
                # 1-2 candidates are surfaced as alternatives and deserve the
                # model's own words too. A reason that names a slot code or
                # writes English is dropped here; those candidates keep the
                # deterministic reason `rank_slots` attached.
                ctx.llm_reasons = {
                    str(c.slot_id): c.reason
                    for c in output.candidates
                    if is_acceptable_llm_reason(c.reason)
                }
                chosen_row = next(
                    (
                        c
                        for c in ctx.ranked_candidates
                        if uuid_mod.UUID(str(c["id"])) == top_pick.slot_id
                    ),
                    None,
                )
                ctx.last_decision_reason = ctx.llm_reasons.get(
                    str(top_pick.slot_id)
                ) or build_reason(
                    chosen_row or {"id": str(top_pick.slot_id)},
                    item,
                    score_terms=(chosen_row or {}).get("score_terms"),
                )
                chosen = {**top_pick.model_dump(mode="json")}
        except Exception as exc:
            # Provider blew up — treat as a transient failure.
            ctx.last_failure = f"rank_candidates failed: {exc!s}"
        return AgentStepResult(
            state=RecommendationState.DECIDE,
            started_at=start,
            ended_at=self._now(),
            payload={
                "chosen_slot_id": str(ctx.chosen_slot_id) if ctx.chosen_slot_id else None,
                "reason": ctx.last_decision_reason,
                "reasons_by_slot": dict(ctx.llm_reasons),
                "last_failure_was": ctx.last_failure,
                "raw_pick": chosen,
                "prompt_hash": prompt_hash,
            },
        )

    async def _step_verify(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        if ctx.verification_ctx is None:
            ctx.verification_ctx = self._build_verification_ctx(ctx)
        if ctx.chosen_slot_id is None:
            return AgentStepResult(
                state=RecommendationState.VERIFY,
                started_at=start,
                ended_at=self._now(),
                payload={"ok": False, "error": "no chosen slot"},
            )
        chosen_slot = next(
            (c for c in ctx.candidates if uuid_mod.UUID(str(c["id"])) == ctx.chosen_slot_id),
            None,
        )
        if chosen_slot is None:
            return AgentStepResult(
                state=RecommendationState.VERIFY,
                started_at=start,
                ended_at=self._now(),
                payload={"ok": False, "error": "chosen slot not in candidate set"},
            )
        chosen_slot = {**chosen_slot, "reason": ctx.last_decision_reason or ""}
        result = run_verifier(ctx.verification_ctx, chosen_slot)
        if not result.ok:
            ctx.last_failure = result.message
        return AgentStepResult(
            state=RecommendationState.VERIFY,
            started_at=start,
            ended_at=self._now(),
            payload={
                "ok": result.ok,
                "first_failure": result.failed[0].code if result.failed else None,
                "first_message": result.message,
                "retries_used": ctx.retries_used,
            },
        )

    async def _step_retry(self, ctx: AgentContext) -> AgentStepResult:
        start = self._now()
        return AgentStepResult(
            state=RecommendationState.RETRY,
            started_at=start,
            ended_at=self._now(),
            payload={"last_failure": ctx.last_failure, "retries_used": ctx.retries_used},
        )

    # --------------------------------------------------------------- helpers

    def _build_verification_ctx(self, ctx: AgentContext) -> VerificationContext:
        known_ids = frozenset(uuid_mod.UUID(str(c["id"])) for c in ctx.candidates)
        active_count: dict[uuid_mod.UUID, int] = {}
        for c in ctx.raw_slots:
            try:
                sid = uuid_mod.UUID(str(c["id"]))
            except (KeyError, ValueError):
                continue
            active_count[sid] = int(c.get("active_count", 0))
        slots_by_id = {uuid_mod.UUID(str(c["id"])): c for c in ctx.raw_slots}
        hard_rules = tuple(r for r in ctx.rules if r.get("rule_type") == "hard")
        return VerificationContext(
            home_id=ctx.home_id,
            known_slot_ids=known_ids,
            whitelist_slot_ids=known_ids,
            slots_by_id=slots_by_id,
            active_count=active_count,
            item=ctx.item or {},
            hard_rules=hard_rules,
            user_preferences=tuple(ctx.preferences),
            history_items=tuple(ctx.history),
        )


__all__ = ["AgentRunResult", "RecommendationAgent"]
