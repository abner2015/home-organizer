"""Unit tests for :mod:`app.services.structure_service`.

The API tests exercise these paths through HTTP; what is only visible here is
the shape of the return values and the *scoping* rules — that ``sort_order`` is
counted per parent (not per home), and that every loader answers with the ORM
row belonging to the caller's home.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.services import structure_service

pytestmark = pytest.mark.asyncio


async def test_loaders_return_rows_in_the_callers_home(
    db_session: AsyncSession, seeded_actor, storage_hierarchy
) -> None:
    room = await structure_service.load_room(
        db_session, room_id=storage_hierarchy.kitchen_room_id, home_id=seeded_actor.home_id
    )
    assert room.name == "厨房"

    unit = await structure_service.load_unit(
        db_session, unit_id=storage_hierarchy.kitchen_cabinet_id, home_id=seeded_actor.home_id
    )
    assert unit.name == "厨房吊柜"

    section_id = unit.sections[0].id
    section = await structure_service.load_section(
        db_session, section_id=section_id, home_id=seeded_actor.home_id
    )
    assert section.unit_id == unit.id

    slot = await structure_service.load_slot(
        db_session,
        slot_id=storage_hierarchy.slots["L1S1"],
        home_id=seeded_actor.home_id,
    )
    assert slot.code == "L1S1"


async def test_loaders_404_on_a_foreign_home(
    db_session: AsyncSession, seeded_actor, storage_hierarchy
) -> None:
    """Cross-home rows are indistinguishable from missing ones."""
    other_home = uuid.uuid4()

    with pytest.raises(NotFoundError):
        await structure_service.load_room(
            db_session, room_id=storage_hierarchy.kitchen_room_id, home_id=other_home
        )
    with pytest.raises(NotFoundError):
        await structure_service.load_unit(
            db_session, unit_id=storage_hierarchy.kitchen_cabinet_id, home_id=other_home
        )
    with pytest.raises(NotFoundError):
        await structure_service.load_slot(
            db_session, slot_id=storage_hierarchy.slots["L1S1"], home_id=other_home
        )


async def test_sort_order_is_counted_per_parent(
    db_session: AsyncSession, seeded_actor
) -> None:
    """Two rooms each start at 0, and their units do not share a counter.

    A global counter would interleave siblings from unrelated rooms the moment
    the user adds a second room, which is exactly the determinism trap the
    ranker hit with all-zero sort orders.
    """
    first = await structure_service.create_room(
        db_session, home_id=seeded_actor.home_id, name="书房", room_type="study"
    )
    second = await structure_service.create_room(
        db_session, home_id=seeded_actor.home_id, name="储藏间", room_type="storage"
    )
    assert (first.sort_order, second.sort_order) == (0, 1)

    shelf = await structure_service.create_unit(
        db_session,
        home_id=seeded_actor.home_id,
        room_id=first.id,
        name="书架",
        unit_type="shelf",
    )
    cabinet = await structure_service.create_unit(
        db_session,
        home_id=seeded_actor.home_id,
        room_id=second.id,
        name="储物柜",
        unit_type="cabinet",
    )
    assert (shelf.sort_order, cabinet.sort_order) == (0, 0)


async def test_create_slot_rejects_a_taken_code(
    db_session: AsyncSession, seeded_actor, storage_hierarchy
) -> None:
    section_id = storage_hierarchy.slots["L1S1"]
    slot = await structure_service.load_slot(
        db_session, slot_id=section_id, home_id=seeded_actor.home_id
    )

    with pytest.raises(ConflictError) as excinfo:
        await structure_service.create_slot(
            db_session,
            home_id=seeded_actor.home_id,
            section_id=slot.section_id,
            code="L1S1",
        )
    assert excinfo.value.details["code"] == "L1S1"


async def test_delete_guards_report_the_blocking_count(
    db_session: AsyncSession, seeded_actor, storage_hierarchy
) -> None:
    with pytest.raises(ConflictError) as excinfo:
        await structure_service.delete_room(
            db_session,
            room_id=storage_hierarchy.living_room_id,
            home_id=seeded_actor.home_id,
        )
    assert excinfo.value.details["unit_count"] == 1

    with pytest.raises(ConflictError) as excinfo:
        await structure_service.delete_unit(
            db_session,
            unit_id=storage_hierarchy.cabinet_id,
            home_id=seeded_actor.home_id,
        )
    assert excinfo.value.details["section_count"] == 3


async def test_update_slot_can_clear_a_nullable_field(
    db_session: AsyncSession, seeded_actor, storage_hierarchy
) -> None:
    slot = await structure_service.update_slot(
        db_session,
        slot_id=storage_hierarchy.slots["L1"],
        home_id=seeded_actor.home_id,
        changes={"label": None},
    )
    assert slot.label is None
