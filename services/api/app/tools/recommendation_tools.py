"""Read-side recommendation helpers — the reject-exclusion set (P0.4).

A user who rejects a recommendation has said "not this slot, for this item".
That is the only durable signal the pipeline can act on, and it is *derived*
rather than stored: ``reject_recommendation`` deliberately leaves
``chosen_slot_id`` in place, so every rejected Recommendation row is a record
of one slot the user vetoed for one item.

This lives in ``app/tools/`` (not ``app/agents/``) for the same reason
``write_tools._create_placement`` does — the dependency direction is
tools → agents, never the reverse. It is deliberately **not** part of
``ToolRegistry``: the registry is the agent's LLM-facing toolbox, and this is
a deterministic pre-filter the pipeline and the no-LLM candidates endpoint
both call directly.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import RecommendationStatus
from app.models.item import Item
from app.models.recommendation import Recommendation


async def get_rejected_slot_ids(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    item_id: uuid.UUID,
    **_: Any,
) -> frozenset[uuid.UUID]:
    """Slots the user has explicitly rejected *for this item*.

    Scoped per item, not per home: rejecting a slot for a bulky item says
    nothing about whether a small item belongs there. Home scope is enforced
    by joining ``Item`` (a Recommendation has no ``home_id`` of its own), so a
    foreign item id yields an empty set rather than another home's vetoes.
    """
    result = await db.execute(
        select(Recommendation.chosen_slot_id)
        .join(Item, Item.id == Recommendation.item_id)
        .where(
            Item.home_id == home_id,
            Recommendation.item_id == item_id,
            Recommendation.status == RecommendationStatus.REJECTED.value,
            Recommendation.chosen_slot_id.is_not(None),
        )
    )
    return frozenset(
        slot_id for slot_id in result.scalars().all() if slot_id is not None
    )


__all__ = ["get_rejected_slot_ids"]
