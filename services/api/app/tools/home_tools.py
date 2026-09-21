"""Home-side tools — fetch the home + storage hierarchy for the agent.

All tools return JSON-safe dicts (UUIDs → strings, datetimes → ISO strings).
Cross-home access raises ``NotFoundError`` so the API can return 404.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.home import Home
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _home_dict(home: Home) -> dict[str, Any]:
    return {
        "id": str(home.id),
        "name": home.name,
        "owner_id": str(home.owner_id),
        "timezone": home.timezone,
        "created_at": _iso(home.created_at),
        "updated_at": _iso(home.updated_at),
    }


def _room_dict(room: Room) -> dict[str, Any]:
    return {
        "id": str(room.id),
        "home_id": str(room.home_id),
        "name": room.name,
        "room_type": room.room_type,
        "sort_order": room.sort_order,
    }


def _unit_dict(unit: StorageUnit) -> dict[str, Any]:
    return {
        "id": str(unit.id),
        "room_id": str(unit.room_id),
        "name": unit.name,
        "unit_type": unit.unit_type,
        "description": unit.description,
        "sort_order": unit.sort_order,
    }


def _section_dict(section: StorageSection) -> dict[str, Any]:
    return {
        "id": str(section.id),
        "unit_id": str(section.unit_id),
        "name": section.name,
        "section_type": section.section_type,
        "sort_order": section.sort_order,
    }


def _slot_dict(slot: StorageSlot) -> dict[str, Any]:
    """One leaf storage position enriched for the LLM.

    Includes parent names/types and the active placement count so the agent
    can reason about capacity without further joins.
    """
    section = slot.section
    unit = section.unit
    room = unit.room
    # Human-facing path. Slot `code`s are ASCII (`L1`, `L1S1`, `LK1`) and used to
    # end every path — which is the English the recommendation UI showed the
    # user. The Chinese `label` ("左玻璃柜第1层") says the same thing readably,
    # so prefer it; `code` stays a separate field for machine identity (the
    # location-hint matcher keys off it).
    leaf = slot.label or f"{section.name}/{slot.code}"
    return {
        "id": str(slot.id),
        "section_id": str(slot.section_id),
        "code": slot.code,
        "label": slot.label,
        "capacity_hint": slot.capacity_hint,
        "allowed_categories": list(slot.allowed_categories),
        "sort_order": slot.sort_order,
        # Enrichment for the LLM
        "home_id": str(room.home_id),
        "room_id": str(room.id),
        "room_name": room.name,
        "room_type": room.room_type,
        "unit_id": str(unit.id),
        "unit_name": unit.name,
        "unit_type": unit.unit_type,
        "section_name": section.name,
        "section_type": section.section_type,
        "full_path": f"{room.name}/{unit.name}/{leaf}",
    }


async def get_home(
    *, db: AsyncSession, home_id: uuid.UUID, **_: Any
) -> dict[str, Any]:
    """Fetch a single home by id. Cross-home access → 404."""
    result = await db.execute(select(Home).where(Home.id == home_id))
    home = result.scalar_one_or_none()
    if home is None:
        raise NotFoundError("Home not found")
    return _home_dict(home)


async def get_rooms(
    *, db: AsyncSession, home_id: uuid.UUID, **_: Any
) -> list[dict[str, Any]]:
    """All rooms in a home."""
    result = await db.execute(
        select(Room).where(Room.home_id == home_id).order_by(Room.sort_order, Room.name)
    )
    return [_room_dict(r) for r in result.scalars().all()]


async def get_storage_units(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    room_id: uuid.UUID | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """All storage units in a home (optionally filtered by room).

    Cross-home access is implicitly protected because the room→home join
    requires the room's ``home_id`` to match ``home_id``.
    """
    stmt = (
        select(StorageUnit)
        .join(Room, Room.id == StorageUnit.room_id)
        .where(Room.home_id == home_id)
        .order_by(StorageUnit.sort_order, StorageUnit.name)
    )
    if room_id is not None:
        stmt = stmt.where(StorageUnit.room_id == room_id)
    result = await db.execute(stmt)
    return [_unit_dict(u) for u in result.scalars().all()]


async def get_storage_sections(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    unit_id: uuid.UUID | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """All sections in a home (optionally filtered by unit)."""
    stmt = (
        select(StorageSection)
        .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
        .join(Room, Room.id == StorageUnit.room_id)
        .where(Room.home_id == home_id)
        .order_by(StorageSection.sort_order, StorageSection.name)
    )
    if unit_id is not None:
        stmt = stmt.where(StorageSection.unit_id == unit_id)
    result = await db.execute(stmt)
    return [_section_dict(s) for s in result.scalars().all()]


async def get_storage_slots(
    *,
    db: AsyncSession,
    home_id: uuid.UUID,
    section_id: uuid.UUID | None = None,
    **_: Any,
) -> list[dict[str, Any]]:
    """All slots in a home, enriched with parent context + active placement count.

    Cross-home access is implicitly protected by the room→home join.
    """
    from sqlalchemy import func as sa_func

    from app.models.placement import ItemPlacement

    stmt = (
        select(
            StorageSlot,
            sa_func.count(ItemPlacement.id).filter(
                ItemPlacement.removed_at.is_(None)
            ).label("active_count"),
        )
        .join(StorageSection, StorageSection.id == StorageSlot.section_id)
        .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
        .join(Room, Room.id == StorageUnit.room_id)
        .outerjoin(ItemPlacement, ItemPlacement.slot_id == StorageSlot.id)
        .where(Room.home_id == home_id)
        .group_by(StorageSlot.id)
        .order_by(StorageSlot.sort_order, StorageSlot.code)
    )
    if section_id is not None:
        stmt = stmt.where(StorageSlot.section_id == section_id)
    rows = (await db.execute(stmt)).all()
    return [
        {**_slot_dict(slot), "active_count": int(active_count or 0)}
        for slot, active_count in rows
    ]


__all__ = [
    "get_home",
    "get_rooms",
    "get_storage_sections",
    "get_storage_slots",
    "get_storage_units",
]
