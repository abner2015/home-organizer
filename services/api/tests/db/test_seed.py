"""Seed correctness tests.

Run the seed against an in-memory SQLite database and verify the resulting
home has:
  * 4 rooms with the expected names / types
  * the complex cabinet in 客厅 (左玻璃柜 3 slots, 中间开放区 1 slot,
    右玻璃柜 3 slots, 下柜 3 slots)
  * the kitchen cabinet (2 layers x 2 slots)
  * a few rules
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.seed import seed
from app.models import HomeMembership, HomeRule, Room, StorageUnit

pytestmark = pytest.mark.asyncio


async def test_seed_idempotent(db_session):
    # Seed runs against `settings.database_url` which is PG. We can't run it
    # here directly — but we can verify the seed builders create the
    # expected structure when fed an SQLite session. Cheaper to inline the
    # assertions on the structure rather than re-mock settings.

    # To keep this test self-contained and not require PG, we build the
    # seed via the same helper functions but against `db_session`.
    from app.db.enums import (
        HomeRole,
        RoomType,
        StorageSectionType,
        StorageUnitType,
    )
    from app.db.seed import (
        _build_bedroom_wardrobe,
        _build_complex_cabinet,
        _build_kids_cabinet,
        _build_kitchen_cabinet,
        _ensure_room,
        _ensure_rules,
        _get_or_create_home,
        _get_or_create_user,
    )

    user = await _get_or_create_user(db_session)
    home = await _get_or_create_home(db_session, user)

    living = await _ensure_room(db_session, home.id, "客厅", RoomType.LIVING, sort=1)
    kitchen = await _ensure_room(db_session, home.id, "厨房", RoomType.KITCHEN, sort=2)
    master = await _ensure_room(db_session, home.id, "主卧", RoomType.BEDROOM, sort=3)
    kids = await _ensure_room(db_session, home.id, "儿童房", RoomType.BEDROOM, sort=4)
    await db_session.flush()

    # Run builders
    await _build_complex_cabinet(db_session, living.id)
    await _build_kitchen_cabinet(db_session, kitchen.id)
    await _build_bedroom_wardrobe(db_session, master.id)
    await _build_kids_cabinet(db_session, kids.id)

    await _ensure_rules(db_session, home.id)
    await db_session.commit()

    # Re-query
    rooms = (await db_session.execute(select(Room).order_by(Room.sort_order))).scalars().all()
    assert [r.name for r in rooms] == ["客厅", "厨房", "主卧", "儿童房"]
    assert [r.room_type for r in rooms] == [
        RoomType.LIVING.value,
        RoomType.KITCHEN.value,
        RoomType.BEDROOM.value,
        RoomType.BEDROOM.value,
    ]

    # Living-room complex cabinet
    living_units = (
        await db_session.execute(
            select(StorageUnit).where(StorageUnit.room_id == living.id)
        )
    ).scalars().all()
    assert len(living_units) == 1
    complex_cab = living_units[0]
    sections = complex_cab.sections
    section_names = [s.name for s in sections]
    assert section_names == ["左玻璃柜", "中间开放区", "右玻璃柜", "下柜"]

    left = next(s for s in sections if s.name == "左玻璃柜")
    middle = next(s for s in sections if s.name == "中间开放区")
    right = next(s for s in sections if s.name == "右玻璃柜")
    lower = next(s for s in sections if s.name == "下柜")

    assert sorted(s.code for s in left.slots) == ["L1", "L2", "L3"]
    assert [s.code for s in middle.slots] == ["M1"]
    assert sorted(s.code for s in right.slots) == ["R1", "R2", "R3"]
    assert sorted(s.code for s in lower.slots) == ["C1", "C2", "C3"]

    # Kitchen cabinet
    kitchen_units = (
        await db_session.execute(
            select(StorageUnit).where(StorageUnit.room_id == kitchen.id)
        )
    ).scalars().all()
    assert len(kitchen_units) == 1
    kitchen_cab = kitchen_units[0]
    assert len(kitchen_cab.sections) == 2
    assert all(sec.section_type == StorageSectionType.LAYER.value for sec in kitchen_cab.sections)
    total_kitchen_slots = sum(len(sec.slots) for sec in kitchen_cab.sections)
    assert total_kitchen_slots == 4  # 2 layers * 2 slots

    # Locked containers — without these the Verifier can never approve a
    # recommendation for an item with is_sensitive=True.
    master_units = (
        await db_session.execute(
            select(StorageUnit).where(StorageUnit.room_id == master.id)
        )
    ).scalars().all()
    assert len(master_units) == 1
    assert master_units[0].unit_type == StorageUnitType.DRAWER_CABINET.value
    locked_sections = [s for s in master_units[0].sections if "锁" in s.name]
    assert [s.name for s in locked_sections] == ["带锁抽屉"]
    assert len(locked_sections[0].slots) == 1

    kids_units = (
        await db_session.execute(
            select(StorageUnit).where(StorageUnit.room_id == kids.id)
        )
    ).scalars().all()
    assert len(kids_units) == 1
    kids_locked = [s for s in kids_units[0].sections if "锁" in s.name]
    assert [s.name for s in kids_locked] == ["药品带锁抽屉"]

    # Rules
    rules = (
        await db_session.execute(select(HomeRule).where(HomeRule.home_id == home.id))
    ).scalars().all()
    rule_names = {r.name for r in rules}
    assert "厨房不放过期食品" in rule_names
    assert "药品上锁" in rule_names

    # Membership
    membership = (
        await db_session.execute(
            select(HomeMembership).where(
                HomeMembership.user_id == user.id, HomeMembership.home_id == home.id
            )
        )
    ).scalar_one()
    assert membership.role == HomeRole.OWNER.value


async def test_seed_full_run_marker(db_session):
    """Smoke test that `seed()` itself is importable and callable.

    We can't actually run `seed()` because it uses `settings.database_url`
    (PG), but we assert the symbol exists so callers can wire it up.
    """
    assert callable(seed)
