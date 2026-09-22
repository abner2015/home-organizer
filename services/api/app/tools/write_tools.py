"""Write-side tools — persist Recommendation / AgentTrace / ItemPlacement rows.

These are the only tools that mutate state. The agent pipeline writes the
Recommendation row at the end of a successful run; the placement_service
writes the ItemPlacement at accept time.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.db.base import utc_now
from app.db.enums import (
    AgentTraceStatus,
    PlacementSource,
    RecommendationStatus,
)
from app.models.item import Item
from app.models.placement import ItemPlacement
from app.models.recommendation import Recommendation
from app.models.trace import AgentTrace


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _recommendation_dict(r: Recommendation) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "item_id": str(r.item_id),
        "agent_trace_id": str(r.agent_trace_id),
        "candidates": list(r.candidates),
        "pre_filter_count": r.pre_filter_count,
        "post_filter_count": r.post_filter_count,
        "chosen_slot_id": str(r.chosen_slot_id) if r.chosen_slot_id else None,
        "status": r.status,
        "created_at": _iso(r.created_at),
    }


def _trace_dict(t: AgentTrace) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "item_id": str(t.item_id) if t.item_id else None,
        "user_id": str(t.user_id),
        "home_id": str(t.home_id),
        "steps": list(t.steps),
        "final_status": t.final_status,
        "total_duration_ms": t.total_duration_ms,
        "llm_tokens_in": t.llm_tokens_in,
        "llm_tokens_out": t.llm_tokens_out,
        "llm_cost_usd": float(t.llm_cost_usd) if t.llm_cost_usd is not None else None,
        "error": t.error,
        "created_at": _iso(t.created_at),
    }


def _placement_dict(p: ItemPlacement) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "item_id": str(p.item_id),
        "slot_id": str(p.slot_id),
        "placed_at": _iso(p.placed_at),
        "removed_at": _iso(p.removed_at),
        "is_active": p.removed_at is None,
        "placed_by": str(p.placed_by),
        "source": p.source,
        "recommendation_id": str(p.recommendation_id) if p.recommendation_id else None,
        "note": p.note,
    }


async def _ensure_item_in_home(
    db: AsyncSession, item_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    result = await db.execute(
        select(Item).where(Item.id == item_id, Item.home_id == home_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Item not found")


async def _ensure_slot_in_home(
    db: AsyncSession, slot_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    """Verify the slot exists and belongs to the given home (cross-home → 404).

    Delegates to ``structure_service.load_slot`` — the write API needs the same
    lookup and returning the row, so keeping a second copy of the four-table
    join here would let the two drift apart.
    """
    from app.services.structure_service import load_slot

    await load_slot(db, slot_id=slot_id, home_id=home_id)


async def create_recommendation(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    agent_trace_id: uuid.UUID,
    candidates: list[dict[str, Any]],
    chosen_slot_id: uuid.UUID | None,
    pre_filter_count: int,
    post_filter_count: int,
    status: str = RecommendationStatus.PENDING.value,
    **_: Any,
) -> dict[str, Any]:
    """Insert one Recommendation row linked to its AgentTrace + Item."""
    await _ensure_item_in_home(db, item_id, home_id)
    if chosen_slot_id is not None:
        await _ensure_slot_in_home(db, chosen_slot_id, home_id)
    # Defensive enum check — keep the CHECK constraint honest from Python too.
    valid_statuses = {s.value for s in RecommendationStatus}
    if status not in valid_statuses:
        raise ValidationFailedError(
            f"Invalid recommendation status {status!r}",
            details={"allowed": sorted(valid_statuses)},
        )
    rec = Recommendation(
        item_id=item_id,
        agent_trace_id=agent_trace_id,
        candidates=candidates,
        chosen_slot_id=chosen_slot_id,
        pre_filter_count=pre_filter_count,
        post_filter_count=post_filter_count,
        status=status,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return _recommendation_dict(rec)


async def verify_recommendation(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    agent_trace_id: uuid.UUID,
    final_status: str = AgentTraceStatus.SUCCESS.value,
    total_duration_ms: int,
    steps: list[dict[str, Any]],
    llm_tokens_in: int | None = None,
    llm_tokens_out: int | None = None,
    llm_cost_usd: float | None = None,
    error: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persist the AgentTrace for a run. ``home_id``/``user_id`` are recorded
    for observability; ``agent_trace_id`` is supplied by the orchestrator
    (the trace is created before any tool call so verify_recommendation just
    updates an existing row).
    """
    valid_statuses = {s.value for s in AgentTraceStatus}
    if final_status not in valid_statuses:
        raise ValidationFailedError(
            f"Invalid trace status {final_status!r}",
            details={"allowed": sorted(valid_statuses)},
        )
    # Either create a new trace (id=None) or update an existing one (id set).
    if agent_trace_id is None:
        trace = AgentTrace(
            home_id=home_id,
            user_id=user_id,
            steps=steps,
            final_status=final_status,
            total_duration_ms=total_duration_ms,
            llm_tokens_in=llm_tokens_in,
            llm_tokens_out=llm_tokens_out,
            llm_cost_usd=llm_cost_usd,
            error=error,
        )
        db.add(trace)
    else:
        result = await db.execute(
            select(AgentTrace).where(AgentTrace.id == agent_trace_id)
        )
        trace = result.scalar_one_or_none()
        if trace is None:
            # Caller passed a planned id; create one with that exact id so
            # the Recommendation FK matches.
            trace = AgentTrace(
                id=agent_trace_id,
                home_id=home_id,
                user_id=user_id,
                steps=steps,
                final_status=final_status,
                total_duration_ms=total_duration_ms,
                llm_tokens_in=llm_tokens_in,
                llm_tokens_out=llm_tokens_out,
                llm_cost_usd=llm_cost_usd,
                error=error,
            )
            db.add(trace)
        else:
            trace.steps = steps
            trace.final_status = final_status
            trace.total_duration_ms = total_duration_ms
            trace.llm_tokens_in = llm_tokens_in
            trace.llm_tokens_out = llm_tokens_out
            trace.llm_cost_usd = llm_cost_usd
            trace.error = error
    await db.commit()
    await db.refresh(trace)
    return _trace_dict(trace)


async def close_active_placements(
    *,
    db: AsyncSession,
    item_id: uuid.UUID,
    now: datetime | None = None,
) -> None:
    """Soft-close every active placement for the item.

    Flush-level only — the caller owns the transaction. One item has at most
    one active placement (partial unique index ``uq_item_placements_one_active_per_item``),
    but we close *all* matching rows rather than one so a database that never
    enforced the index (SQLite in tests) still converges to a single active row.
    """
    from sqlalchemy import update

    await db.execute(
        update(ItemPlacement)
        .where(
            ItemPlacement.item_id == item_id,
            ItemPlacement.removed_at.is_(None),
        )
        .values(removed_at=now or utc_now())
    )


async def _create_placement(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    slot_id: uuid.UUID,
    recommendation_id: uuid.UUID | None = None,
    source: str = PlacementSource.USER_MANUAL.value,
    note: str | None = None,
    remove_existing: bool = True,
) -> ItemPlacement:
    """The one implementation of "close the old active placement, insert the new one".

    Validates item/slot ownership (cross-home → 404) and the ``source`` enum,
    then flushes. The caller owns the commit: the accept path needs the
    placement, the status flip and the supersede sweep to land as one
    transaction, while the manual path commits on its own.
    """
    await _ensure_item_in_home(db, item_id, home_id)
    await _ensure_slot_in_home(db, slot_id, home_id)

    valid_sources = {s.value for s in PlacementSource}
    if source not in valid_sources:
        raise ValidationFailedError(
            f"Invalid placement source {source!r}",
            details={"allowed": sorted(valid_sources)},
        )

    if remove_existing:
        await close_active_placements(db=db, item_id=item_id)

    placement = ItemPlacement(
        item_id=item_id,
        slot_id=slot_id,
        placed_by=user_id,
        recommendation_id=recommendation_id,
        source=source,
        note=note,
    )
    db.add(placement)
    await db.flush()
    return placement


async def save_placement(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    item_id: uuid.UUID,
    slot_id: uuid.UUID,
    recommendation_id: uuid.UUID | None = None,
    source: str = PlacementSource.USER_MANUAL.value,
    note: str | None = None,
    remove_existing: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """Create a new ItemPlacement row. By default removes any active placement
    for the same item (single-active-placement invariant from the DB partial
    unique index)."""
    placement = await _create_placement(
        db=db,
        home_id=home_id,
        user_id=user_id,
        item_id=item_id,
        slot_id=slot_id,
        recommendation_id=recommendation_id,
        source=source,
        note=note,
        remove_existing=remove_existing,
    )
    await db.commit()
    await db.refresh(placement)
    return _placement_dict(placement)


__all__ = [
    "close_active_placements",
    "create_recommendation",
    "save_placement",
    "verify_recommendation",
]
