"""Item-side tools — fetch items and their placement history.

All tools return JSON-safe dicts. ``search_items`` is a simple substring +
category filter; future phases can replace it with a vector search.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.item import Item
from app.models.placement import ItemPlacement


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _item_dict(item: Item) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "home_id": str(item.home_id),
        "name": item.name,
        "description": item.description,
        "category": item.category,
        "subcategory": item.subcategory,
        "brand": item.brand,
        "estimated_size": item.estimated_size,
        "is_sensitive": item.is_sensitive,
        "needs_lock": item.needs_lock,
        "primary_image_id": str(item.primary_image_id) if item.primary_image_id else None,
        "created_by": str(item.created_by),
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
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


async def get_items(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    item_id: uuid.UUID | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Items in a home, or a single item by id.

    A single-id lookup is funneled through ``list[dict]`` so the LLM sees a
    uniform shape; passing a non-existent ``item_id`` raises ``NotFoundError``
    only when the caller is asking for one item.
    """
    if item_id is not None:
        result = await db.execute(
            select(Item).where(Item.id == item_id, Item.home_id == home_id)
        )
        item = result.scalar_one_or_none()
        if item is None:
            raise NotFoundError("Item not found")
        return [_item_dict(item)]
    result = await db.execute(
        select(Item).where(Item.home_id == home_id).order_by(Item.created_at.desc())
    )
    return [_item_dict(i) for i in result.scalars().all()]


async def search_items(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    query: str | None = None,
    category: str | None = None,
    room_name: str | None = None,
    is_sensitive: bool | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """Substring match on name + exact category filter + room / sensitivity filters.

    When ``room_name`` is set, only items currently placed in a slot whose
    containing room name matches (case-insensitive substring) are returned;
    items with no active placement are excluded. This powers the
    "厨房里有什么工具？" class of natural-language queries.

    Empty result = empty list, not an error — call sites decide whether
    "no matches" is a hard failure.
    """
    stmt = select(Item).where(Item.home_id == home_id)
    if query:
        stmt = stmt.where(Item.name.ilike(f"%{query}%"))
    if category:
        stmt = stmt.where(Item.category == category)
    if is_sensitive is not None:
        stmt = stmt.where(Item.is_sensitive == is_sensitive)
    if room_name:
        from app.models.room import Room
        from app.models.storage import StorageSection, StorageSlot, StorageUnit

        # Join active placements → slot → section → unit → room.
        # Use ``EXISTS`` so the room filter applies as a subquery and we don't
        # duplicate Item rows when an item has multiple active placements (it
        # shouldn't, but defensive).
        stmt = stmt.where(
            select(ItemPlacement.id)
            .where(
                ItemPlacement.item_id == Item.id,
                ItemPlacement.removed_at.is_(None),
            )
            .join(StorageSlot, StorageSlot.id == ItemPlacement.slot_id)
            .join(StorageSection, StorageSection.id == StorageSlot.section_id)
            .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
            .join(Room, Room.id == StorageUnit.room_id)
            .where(Room.name.ilike(f"%{room_name}%"))
            .exists()
        )
    result = await db.execute(stmt.order_by(Item.created_at.desc()))
    return [_item_dict(i) for i in result.scalars().all()]


async def get_item_placements(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    item_id: uuid.UUID | None = None,
    slot_id: uuid.UUID | None = None,
    active_only: bool = False,
    **_: Any,
) -> list[dict[str, Any]]:
    """Placement history for an item or a slot (cross-home safe)."""
    stmt = (
        select(ItemPlacement)
        .join(Item, Item.id == ItemPlacement.item_id)
        .where(Item.home_id == home_id)
    )
    if item_id is not None:
        stmt = stmt.where(ItemPlacement.item_id == item_id)
    if slot_id is not None:
        stmt = stmt.where(ItemPlacement.slot_id == slot_id)
    if active_only:
        stmt = stmt.where(ItemPlacement.removed_at.is_(None))
    stmt = stmt.order_by(ItemPlacement.placed_at.desc())
    result = await db.execute(stmt)
    return [_placement_dict(p) for p in result.scalars().all()]


__all__ = ["get_item_placements", "get_items", "search_items"]
