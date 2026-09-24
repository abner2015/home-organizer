"""Project ORM rows of the storage hierarchy into response view dicts.

The four ``*_with_enrichment`` functions below are used by both the detail
read endpoints (:mod:`app.api.v1.homes`) and the write endpoints
(:mod:`app.api.v1.structure`). Before P0.C they were private helpers inside
``structure.py``; the read side needed the same projection logic (a fresh
PATCH response shape, a GET detail shape), so the helpers were hoisted here
as the single source of truth.

Names follow the pattern ``<entity>_with_enrichment`` so the read side can
ask for "a unit, with its sections and their slots" in one call. ``row_dict``
is the underlying ``ORM row → JSON-shaped dict`` projection used by the
factories in :mod:`app.schemas.home`.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit
from app.schemas.home import (
    StorageSectionView,
    StorageSlotView,
    section_view,
    slot_view,
)
from app.tools.home_tools import (
    get_storage_sections,
    get_storage_slots,
    get_storage_units,
)

if TYPE_CHECKING:
    from app.schemas.home import RoomView, StorageUnitView

# Column whitelist per entity. Listed explicitly so the view can never leak a
# column the API doesn't promise (e.g. ``created_at``); keeping the tuples
# private to this module means ``app.schemas.home`` stays pure-Pydantic.
ROOM_FIELDS: tuple[str, ...] = ("id", "home_id", "name", "room_type", "sort_order")
UNIT_FIELDS: tuple[str, ...] = (
    "id",
    "room_id",
    "name",
    "unit_type",
    "description",
    "sort_order",
)
SECTION_FIELDS: tuple[str, ...] = ("id", "unit_id", "name", "section_type", "sort_order")
SLOT_FIELDS: tuple[str, ...] = (
    "id",
    "section_id",
    "code",
    "label",
    "capacity_hint",
    "allowed_categories",
    "sort_order",
)


def row_dict(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """ORM row → the JSON-shaped dict :mod:`app.schemas.home`'s factories read."""
    return {name: getattr(row, name, None) for name in fields}


async def slot_view_with_enrichment(
    db: AsyncSession, *, home_id: uuid.UUID, slot: StorageSlot
) -> StorageSlotView:
    """Load ``slot``'s siblings so the view can carry its ``active_count``."""
    siblings = await get_storage_slots(db=db, home_id=home_id, section_id=slot.section_id)
    enriched = next(
        (item for item in siblings if item["id"] == str(slot.id)),
        row_dict(slot, SLOT_FIELDS),
    )
    return slot_view(enriched)


async def section_view_with_slots(
    db: AsyncSession, *, home_id: uuid.UUID, section: StorageSection
) -> StorageSectionView:
    """Project ``section`` together with all its slots."""
    slots = await get_storage_slots(db=db, home_id=home_id, section_id=section.id)
    return section_view(
        row_dict(section, SECTION_FIELDS), slots=[slot_view(s) for s in slots]
    )


async def unit_view_with_sections(
    db: AsyncSession, *, home_id: uuid.UUID, unit: StorageUnit
) -> StorageUnitView:
    """Project ``unit`` together with its sections and their slots."""
    sections = await get_storage_sections(db=db, home_id=home_id, unit_id=unit.id)
    slots = await get_storage_slots(db=db, home_id=home_id)
    by_section: dict[str, list[StorageSlotView]] = {}
    for slot in slots:
        by_section.setdefault(slot["section_id"], []).append(slot_view(slot))
    from app.schemas.home import unit_view

    return unit_view(
        row_dict(unit, UNIT_FIELDS),
        sections=[
            section_view(section, slots=by_section.get(section["id"], []))
            for section in sections
        ],
    )


async def room_view_with_units(
    db: AsyncSession, *, home_id: uuid.UUID, room: Room
) -> RoomView:
    """Project ``room`` together with its unit count."""
    from app.schemas.home import room_view

    units = await get_storage_units(db=db, home_id=home_id, room_id=room.id)
    return room_view(row_dict(room, ROOM_FIELDS), unit_count=len(units))


__all__ = [
    "ROOM_FIELDS",
    "SECTION_FIELDS",
    "SLOT_FIELDS",
    "UNIT_FIELDS",
    "room_view_with_units",
    "row_dict",
    "section_view_with_slots",
    "slot_view_with_enrichment",
    "unit_view_with_sections",
]
