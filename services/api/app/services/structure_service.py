"""Write side of the storage hierarchy: rooms, units, sections, slots.

Nothing could *create* storage structure before this module — the whole
hierarchy came from ``python -m app.db.seed``. A freshly signed-up account
therefore had an empty tree, no slot was ever reachable, and the recommendation
agent could only answer ``state=failed`` with 「无符合硬规则的位置」. This is the
write half of :mod:`app.tools.home_tools` and obeys its one rule: every query is
scoped by ``home_id``, so a row in somebody else's home is indistinguishable
from a row that does not exist (404, never 403).

Functions return ORM rows and end with ``flush()``; the caller commits. That
matches :mod:`app.api.v1.items` and lets a route read back a generated id
before the transaction closes.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.placement import ItemPlacement
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit

# --------------------------------------------------------------------- loaders
#
# Each loader joins all the way up to ``rooms`` so the ``home_id`` filter is
# part of the same statement. Letting a caller fetch by id and then compare
# ``row.home_id`` would work too, but the compare is easy to forget; here the
# cross-home case simply returns no row.


async def load_room(
    db: AsyncSession, *, room_id: uuid.UUID, home_id: uuid.UUID
) -> Room:
    """Fetch a room in ``home_id``. Unknown or another home's → 404."""
    room = (
        await db.execute(
            select(Room).where(Room.id == room_id, Room.home_id == home_id)
        )
    ).scalar_one_or_none()
    if room is None:
        raise NotFoundError("Room not found")
    return room


async def load_unit(
    db: AsyncSession, *, unit_id: uuid.UUID, home_id: uuid.UUID
) -> StorageUnit:
    """Fetch a storage unit in ``home_id``. Unknown or another home's → 404."""
    unit = (
        await db.execute(
            select(StorageUnit)
            .join(Room, Room.id == StorageUnit.room_id)
            .where(StorageUnit.id == unit_id, Room.home_id == home_id)
        )
    ).scalar_one_or_none()
    if unit is None:
        raise NotFoundError("Storage unit not found")
    return unit


async def load_section(
    db: AsyncSession, *, section_id: uuid.UUID, home_id: uuid.UUID
) -> StorageSection:
    """Fetch a section in ``home_id``. Unknown or another home's → 404."""
    section = (
        await db.execute(
            select(StorageSection)
            .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
            .join(Room, Room.id == StorageUnit.room_id)
            .where(StorageSection.id == section_id, Room.home_id == home_id)
        )
    ).scalar_one_or_none()
    if section is None:
        raise NotFoundError("Storage section not found")
    return section


async def load_slot(
    db: AsyncSession, *, slot_id: uuid.UUID, home_id: uuid.UUID
) -> StorageSlot:
    """Fetch a slot in ``home_id``. Unknown or another home's → 404.

    The message is pinned by ``tests/unit/test_tools.py``; the tool layer
    delegates here rather than keeping its own copy of the query.
    """
    slot = (
        await db.execute(
            select(StorageSlot)
            .join(StorageSection, StorageSection.id == StorageSlot.section_id)
            .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
            .join(Room, Room.id == StorageUnit.room_id)
            .where(StorageSlot.id == slot_id, Room.home_id == home_id)
        )
    ).scalar_one_or_none()
    if slot is None:
        raise NotFoundError("Storage slot not found")
    return slot


# ------------------------------------------------------------------ sort order


async def _next_sort_order(
    db: AsyncSession, model: Any, **parent: uuid.UUID
) -> int:
    """``max(sort_order) + 1`` among siblings, or 0 for the first one.

    Leaving every row at the column default 0 makes ``ORDER BY sort_order``
    tie everywhere, at which point the tree renders in whatever order the
    database happens to return — the same determinism trap the ranker hit.
    """
    stmt = select(func.max(model.sort_order)).where(
        *(getattr(model, key) == value for key, value in parent.items())
    )
    current = (await db.execute(stmt)).scalar_one_or_none()
    return int(current) + 1 if current is not None else 0


# -------------------------------------------------------------------- creators
#
# Each creator loads its parent first. That is what turns "attach a section to
# a unit in another household" into a 404 instead of a silently-foreign row.


async def create_room(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    name: str,
    room_type: str,
    sort_order: int | None = None,
) -> Room:
    """Add a room to ``home_id``."""
    if sort_order is None:
        sort_order = await _next_sort_order(db, Room, home_id=home_id)
    room = Room(home_id=home_id, name=name, room_type=room_type, sort_order=sort_order)
    db.add(room)
    await db.flush()
    return room


async def create_unit(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    room_id: uuid.UUID,
    name: str,
    unit_type: str,
    description: str | None = None,
    sort_order: int | None = None,
) -> StorageUnit:
    """Add a storage unit to a room of ``home_id``."""
    room = await load_room(db, room_id=room_id, home_id=home_id)
    if sort_order is None:
        sort_order = await _next_sort_order(db, StorageUnit, room_id=room.id)
    unit = StorageUnit(
        room_id=room.id,
        name=name,
        unit_type=unit_type,
        description=description,
        sort_order=sort_order,
    )
    db.add(unit)
    await db.flush()
    return unit


async def create_section(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    unit_id: uuid.UUID,
    name: str,
    section_type: str,
    sort_order: int | None = None,
) -> StorageSection:
    """Add a section (layer / drawer / compartment) to a unit of ``home_id``."""
    unit = await load_unit(db, unit_id=unit_id, home_id=home_id)
    if sort_order is None:
        sort_order = await _next_sort_order(db, StorageSection, unit_id=unit.id)
    section = StorageSection(
        unit_id=unit.id,
        name=name,
        section_type=section_type,
        sort_order=sort_order,
    )
    db.add(section)
    await db.flush()
    return section


async def create_slot(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    section_id: uuid.UUID,
    code: str,
    label: str | None = None,
    capacity_hint: str | None = None,
    allowed_categories: list[str] | None = None,
    sort_order: int | None = None,
) -> StorageSlot:
    """Add a leaf slot to a section of ``home_id``.

    ``(section_id, code)`` is unique. The clash is checked up front rather than
    caught from the resulting ``IntegrityError``: the pre-check is dialect
    independent, and it can say which code was taken.
    """
    section = await load_section(db, section_id=section_id, home_id=home_id)
    await _ensure_code_available(
        db, section_id=section.id, code=code, exclude_slot_id=None
    )
    if sort_order is None:
        sort_order = await _next_sort_order(db, StorageSlot, section_id=section.id)
    slot = StorageSlot(
        section_id=section.id,
        code=code,
        label=label,
        capacity_hint=capacity_hint,
        allowed_categories=list(allowed_categories or []),
        sort_order=sort_order,
    )
    db.add(slot)
    await db.flush()
    return slot


async def _ensure_code_available(
    db: AsyncSession,
    *,
    section_id: uuid.UUID,
    code: str,
    exclude_slot_id: uuid.UUID | None,
) -> None:
    stmt = select(StorageSlot.id).where(
        StorageSlot.section_id == section_id, StorageSlot.code == code
    )
    if exclude_slot_id is not None:
        stmt = stmt.where(StorageSlot.id != exclude_slot_id)
    if (await db.execute(stmt)).first() is not None:
        raise ConflictError(
            f"该分区下已存在编码为「{code}」的收纳位",
            details={"code": code},
        )


# -------------------------------------------------------------------- updaters


async def _apply(row: Any, changes: Mapping[str, Any]) -> None:
    for field, value in changes.items():
        setattr(row, field, value)


async def update_room(
    db: AsyncSession,
    *,
    room_id: uuid.UUID,
    home_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> Room:
    """Apply a sparse patch. Omitted fields are left untouched."""
    room = await load_room(db, room_id=room_id, home_id=home_id)
    await _apply(room, changes)
    await db.flush()
    return room


async def update_unit(
    db: AsyncSession,
    *,
    unit_id: uuid.UUID,
    home_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> StorageUnit:
    unit = await load_unit(db, unit_id=unit_id, home_id=home_id)
    await _apply(unit, changes)
    await db.flush()
    return unit


async def update_section(
    db: AsyncSession,
    *,
    section_id: uuid.UUID,
    home_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> StorageSection:
    section = await load_section(db, section_id=section_id, home_id=home_id)
    await _apply(section, changes)
    await db.flush()
    return section


async def update_slot(
    db: AsyncSession,
    *,
    slot_id: uuid.UUID,
    home_id: uuid.UUID,
    changes: Mapping[str, Any],
) -> StorageSlot:
    """Apply a sparse patch, re-checking ``code`` uniqueness when it moves."""
    slot = await load_slot(db, slot_id=slot_id, home_id=home_id)
    new_code = changes.get("code")
    if new_code is not None and new_code != slot.code:
        await _ensure_code_available(
            db, section_id=slot.section_id, code=new_code, exclude_slot_id=slot.id
        )
    await _apply(slot, changes)
    await db.flush()
    return slot


# -------------------------------------------------------------------- deleters
#
# Every level refuses to delete while it still has children, in that level's
# own words. Cascading instead would be one careless ``session.delete`` away
# from silently discarding a whole subtree — the ORM relationships are
# ``cascade="all, delete-orphan"``, so nothing would stop it.


async def _child_count(db: AsyncSession, model: Any, *clauses: Any) -> int:
    stmt = select(func.count()).select_from(model).where(*clauses)
    return int((await db.execute(stmt)).scalar_one())


async def delete_room(
    db: AsyncSession, *, room_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    """Delete a room that has no storage units left."""
    room = await load_room(db, room_id=room_id, home_id=home_id)
    count = await _child_count(db, StorageUnit, StorageUnit.room_id == room.id)
    if count:
        raise ConflictError(
            f"该房间下还有 {count} 件收纳家具，不能删除",
            details={"unit_count": count},
        )
    await db.delete(room)
    await db.flush()


async def delete_unit(
    db: AsyncSession, *, unit_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    """Delete a storage unit that has no sections left."""
    unit = await load_unit(db, unit_id=unit_id, home_id=home_id)
    count = await _child_count(db, StorageSection, StorageSection.unit_id == unit.id)
    if count:
        raise ConflictError(
            f"该家具下还有 {count} 个分区，不能删除",
            details={"section_count": count},
        )
    await db.delete(unit)
    await db.flush()


async def delete_section(
    db: AsyncSession, *, section_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    """Delete a section that has no slots left."""
    section = await load_section(db, section_id=section_id, home_id=home_id)
    count = await _child_count(db, StorageSlot, StorageSlot.section_id == section.id)
    if count:
        raise ConflictError(
            f"该分区下还有 {count} 个收纳位，不能删除",
            details={"slot_count": count},
        )
    await db.delete(section)
    await db.flush()


async def delete_slot(
    db: AsyncSession, *, slot_id: uuid.UUID, home_id: uuid.UUID
) -> None:
    """Delete a slot that has never held an item.

    ``item_placements.slot_id`` is ``ondelete="RESTRICT"`` and the constraint
    does not care whether the placement was since removed, so an app-level
    check that only counted *active* placements would pass and then blow up in
    the database with an ``IntegrityError`` (500). History is a real reason to
    keep a position around, so both counts are reported separately.
    """
    slot = await load_slot(db, slot_id=slot_id, home_id=home_id)
    total = await _child_count(db, ItemPlacement, ItemPlacement.slot_id == slot.id)
    if total:
        active = await _child_count(
            db,
            ItemPlacement,
            ItemPlacement.slot_id == slot.id,
            ItemPlacement.removed_at.is_(None),
        )
        raise ConflictError(
            "该收纳位上还有物品记录，不能删除",
            details={"active_count": active, "historical_count": total - active},
        )
    await db.delete(slot)
    await db.flush()


__all__ = [
    "create_room",
    "create_section",
    "create_slot",
    "create_unit",
    "delete_room",
    "delete_section",
    "delete_slot",
    "delete_unit",
    "load_room",
    "load_section",
    "load_slot",
    "load_unit",
    "update_room",
    "update_section",
    "update_slot",
    "update_unit",
]
