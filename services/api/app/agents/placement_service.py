"""Placement service — accept/reject/patch lifecycle for a Recommendation.

This sits between the API endpoints and the write_tools. It enforces the
business invariants the API can't safely inline:

- ``accept`` requires the Recommendation to be ``pending`` and the (possibly
  patched) ``chosen_slot_id`` to belong to the calling home.
- ``accept`` creates one ``ItemPlacement`` row. If the recommendation was
  PATCHed before accept, ``source`` is ``user_manual``; otherwise
  ``ai_recommendation``.
- ``reject`` sets status to ``rejected`` (still requires ``pending``).
- ``patch`` updates ``chosen_slot_id`` (must belong to home) and keeps
  status ``pending``. Any previously-pending recommendation for the same
  item becomes ``superseded`` once the user accepts — but PATCH itself
  doesn't supersede; that happens at accept time to avoid spurious
  history churn.

Cross-home lookups (Recommendation belongs to home A, actor is home B)
surface as :class:`NotFoundError` so the API returns 404 without leaking
existence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.db.enums import PlacementSource, RecommendationStatus
from app.models import Item, ItemPlacement, Recommendation
from app.tools.write_tools import _ensure_slot_in_home

# --------------------------------------------------------------------------- errors


class PlacementError(AppError):
    """Base for placement_service-level errors."""

    code = "placement_error"
    http_status = 400
    message = "Placement operation failed"


# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class AcceptOutcome:
    """Return shape of :func:`accept_recommendation`."""

    recommendation: Recommendation
    placement: ItemPlacement
    was_patched: bool


@dataclass(slots=True)
class RejectOutcome:
    """Return shape of :func:`reject_recommendation`."""

    recommendation: Recommendation


@dataclass(slots=True)
class PatchOutcome:
    """Return shape of :func:`patch_recommendation`."""

    recommendation: Recommendation


# --------------------------------------------------------------------------- helpers


async def _load_recommendation_in_home(
    db: AsyncSession, recommendation_id: uuid.UUID, home_id: uuid.UUID
) -> Recommendation:
    """Cross-home safe load — 404 if missing or owned by another home."""
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
    return rec


async def _ensure_pending(rec: Recommendation) -> None:
    if rec.status != RecommendationStatus.PENDING.value:
        raise ConflictError(
            f"Recommendation is in status {rec.status!r}; "
            f"only 'pending' recommendations can be modified.",
            details={"status": rec.status},
        )


async def _supersede_previous_pending(
    db: AsyncSession, item_id: uuid.UUID, keep_id: uuid.UUID
) -> None:
    """If the item has other ``pending`` recommendations (e.g. the user re-ran
    ``POST /recommend``), flip them to ``superseded`` so the history graph is
    unambiguous. Called at accept time.
    """
    await db.execute(
        update(Recommendation)
        .where(
            Recommendation.item_id == item_id,
            Recommendation.status == RecommendationStatus.PENDING.value,
            Recommendation.id != keep_id,
        )
        .values(status=RecommendationStatus.SUPERSEDED.value)
    )


# --------------------------------------------------------------------------- public API


async def accept_recommendation(
    db: AsyncSession,
    *,
    recommendation_id: uuid.UUID,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    note: str | None = None,
) -> AcceptOutcome:
    """Mark the recommendation ``accepted`` and create an ItemPlacement.

    The slot used is ``rec.chosen_slot_id`` — which is either the original
    AI choice or the user's PATCH override. The placement ``source`` is
    ``user_manual`` if the row was PATCHed before accept, else
    ``ai_recommendation``.

    Raises:
        NotFoundError: recommendation not found or wrong home.
        ConflictError: recommendation is not in ``pending`` status.
        NotFoundError: chosen slot no longer belongs to this home.
    """
    rec = await _load_recommendation_in_home(db, recommendation_id, home_id)
    await _ensure_pending(rec)
    if rec.chosen_slot_id is None:
        raise ConflictError(
            "Recommendation has no chosen slot (agent pipeline failed); "
            "cannot accept.",
            details={"state": "failed"},
        )
    await _ensure_slot_in_home(db, rec.chosen_slot_id, home_id)

    # Was this recommendation PATCHed? PATCH writes the audit_note on the
    # `candidates` JSON blob; absence ⇒ original AI choice.
    was_patched = any(
        isinstance(c, dict) and c.get("slot_id") == str(rec.chosen_slot_id)
        and c.get("audit_note") == "user_patch"
        for c in (rec.candidates or [])
    )

    placement = ItemPlacement(
        item_id=rec.item_id,
        slot_id=rec.chosen_slot_id,
        placed_by=user_id,
        recommendation_id=rec.id,
        source=(
            PlacementSource.USER_MANUAL.value
            if was_patched
            else PlacementSource.AI_RECOMMENDATION.value
        ),
        note=note,
    )
    db.add(placement)
    rec.status = RecommendationStatus.ACCEPTED.value
    await _supersede_previous_pending(db, rec.item_id, rec.id)
    await db.commit()
    await db.refresh(placement)
    await db.refresh(rec)
    return AcceptOutcome(recommendation=rec, placement=placement, was_patched=was_patched)


async def reject_recommendation(
    db: AsyncSession,
    *,
    recommendation_id: uuid.UUID,
    home_id: uuid.UUID,
    note: str | None = None,
) -> RejectOutcome:
    """Mark the recommendation ``rejected``. No placement is created."""
    rec = await _load_recommendation_in_home(db, recommendation_id, home_id)
    await _ensure_pending(rec)
    if note is not None:
        # We have no dedicated `note` column on Recommendation; stash on the
        # first candidate's `audit_note` for traceability (the JSON blob is
        # already used for this purpose by PATCH).
        cands = list(rec.candidates or [])
        if cands and isinstance(cands[0], dict):
            cands[0] = {**cands[0], "audit_note": note}
            rec.candidates = cands  # type: ignore[assignment]
    rec.status = RecommendationStatus.REJECTED.value
    await db.commit()
    await db.refresh(rec)
    return RejectOutcome(recommendation=rec)


async def patch_recommendation(
    db: AsyncSession,
    *,
    recommendation_id: uuid.UUID,
    home_id: uuid.UUID,
    chosen_slot_id: uuid.UUID,
    reason: str | None = None,
) -> PatchOutcome:
    """Let the user override the agent's choice before accepting.

    Validates the new slot belongs to the same home, then swaps
    ``chosen_slot_id`` and stamps an audit marker on the matching candidate
    so :func:`accept_recommendation` can later tell AI vs. user source.
    """
    rec = await _load_recommendation_in_home(db, recommendation_id, home_id)
    await _ensure_pending(rec)
    await _ensure_slot_in_home(db, chosen_slot_id, home_id)

    rec.chosen_slot_id = chosen_slot_id
    # Stamp the audit marker onto the matching candidate slot, and also
    # surface a `user_reason` so the audit trail is complete.
    cands = list(rec.candidates or [])
    new_cands: list[dict[str, Any]] = []
    found = False
    for c in cands:
        if not isinstance(c, dict):
            new_cands.append(c)
            continue
        if str(c.get("slot_id")) == str(chosen_slot_id):
            new_cands.append(
                {**c, "audit_note": "user_patch", "user_reason": reason or ""}
            )
            found = True
        else:
            new_cands.append(c)
    if not found:
        # The user picked a slot the LLM didn't surface. Append a synthetic
        # entry so the audit trail still records the choice.
        new_cands.append(
            {
                "slot_id": str(chosen_slot_id),
                "confidence": 0.0,
                "reason": reason or "",
                "matched_rules": [],
                "evidence_item_ids": [],
                "audit_note": "user_patch",
                "user_reason": reason or "",
            }
        )
    rec.candidates = new_cands  # type: ignore[assignment]
    await db.commit()
    await db.refresh(rec)
    return PatchOutcome(recommendation=rec)


# --------------------------------------------------------------------------- view helpers


async def get_recommendation_dict(
    db: AsyncSession, recommendation_id: uuid.UUID
) -> dict[str, Any] | None:
    """Return the recommendation as the public dict shape, or None."""
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.id == recommendation_id)
        )
    ).scalar_one_or_none()
    if rec is None:
        return None
    return _recommendation_to_dict(rec)


def _recommendation_to_dict(rec: Recommendation) -> dict[str, Any]:
    return {
        "id": str(rec.id),
        "item_id": str(rec.item_id),
        "agent_trace_id": str(rec.agent_trace_id),
        "candidates": list(rec.candidates or []),
        "pre_filter_count": rec.pre_filter_count,
        "post_filter_count": rec.post_filter_count,
        "chosen_slot_id": str(rec.chosen_slot_id) if rec.chosen_slot_id else None,
        "status": rec.status,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
    }


__all__ = [
    "AcceptOutcome",
    "PatchOutcome",
    "PlacementError",
    "RejectOutcome",
    "accept_recommendation",
    "get_recommendation_dict",
    "patch_recommendation",
    "reject_recommendation",
]
