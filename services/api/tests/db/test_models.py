"""ORM model integrity tests.

These tests use SQLite in-memory via the `db_session` fixture from
conftest.py. They verify:
- model defaults (UUID, timestamps)
- FK cascade behavior
- enum CHECK constraints (room_type, unit_type, etc.)
- basic CRUD round-trips
- one-to-many relationship traversal

NOTE: SQLite does NOT enforce PostgreSQL-specific features. The following
must be tested against a real Postgres (see tests/integration/ in a later
phase):
  * Partial unique indexes (uq_item_placements_one_active_per_item,
    uq_item_images_one_primary)
  * JSONB containment queries
  * ARRAY column operators
  * DEFERRABLE FK semantics
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.enums import (
    HomeRole,
    HomeRuleType,
    PlacementSource,
    RoomType,
    StorageSectionType,
    StorageUnitType,
)
from app.models import (
    AgentTrace,
    Home,
    HomeMembership,
    HomeRule,
    Item,
    ItemPlacement,
    Recommendation,
    Room,
    StorageSection,
    StorageSlot,
    StorageUnit,
    User,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- helpers


async def _make_user(session, email="a@example.com") -> User:
    user = User(email=email, password_hash="h", display_name="A")
    session.add(user)
    await session.flush()
    return user


async def _make_home(session, owner: User, name="Home") -> Home:
    home = Home(name=name, owner_id=owner.id)
    session.add(home)
    await session.flush()
    session.add(
        HomeMembership(user_id=owner.id, home_id=home.id, role=HomeRole.OWNER.value)
    )
    await session.flush()
    return home


async def _make_room(session, home: Home, name="R", room_type=RoomType.LIVING) -> Room:
    room = Room(home_id=home.id, name=name, room_type=room_type.value)
    session.add(room)
    await session.flush()
    return room


async def _make_unit(session, room: Room, name="U") -> StorageUnit:
    unit = StorageUnit(room_id=room.id, name=name, unit_type=StorageUnitType.CABINET.value)
    session.add(unit)
    await session.flush()
    return unit


async def _make_section(session, unit: StorageUnit, name="S") -> StorageSection:
    section = StorageSection(
        unit_id=unit.id, name=name, section_type=StorageSectionType.LAYER.value
    )
    session.add(section)
    await session.flush()
    return section


async def _make_slot(session, section: StorageSection, code="A1") -> StorageSlot:
    slot = StorageSlot(section_id=section.id, code=code, allowed_categories=["misc"])
    session.add(slot)
    await session.flush()
    return slot


# --------------------------------------------------------------------------- users / homes


async def test_user_default_uuid_and_timestamps(db_session):
    user = User(email="x@y", password_hash="hash", display_name="X")
    db_session.add(user)
    await db_session.flush()
    assert isinstance(user.id, uuid.UUID)
    assert isinstance(user.created_at, datetime)
    assert user.created_at.tzinfo is not None
    assert user.updated_at.tzinfo is not None


async def test_user_email_unique(db_session):
    db_session.add(User(email="dup@e.com", password_hash="h", display_name="A"))
    await db_session.flush()
    db_session.add(User(email="dup@e.com", password_hash="h", display_name="B"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_home_membership_unique_per_user_home(db_session):
    user = await _make_user(db_session)
    home = Home(name="H", owner_id=user.id)
    db_session.add(home)
    await db_session.flush()
    db_session.add(HomeMembership(user_id=user.id, home_id=home.id, role=HomeRole.OWNER.value))
    await db_session.flush()
    db_session.add(HomeMembership(user_id=user.id, home_id=home.id, role=HomeRole.MEMBER.value))
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------- hierarchy


async def test_storage_hierarchy_traversal(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = await _make_room(db_session, home, name="客厅", room_type=RoomType.LIVING)
    unit = await _make_unit(db_session, room)
    section = await _make_section(db_session, unit)
    slot = await _make_slot(db_session, section)

    # Forward traversal
    assert slot.section is section
    assert slot.section.unit is unit
    assert slot.section.unit.room is room

    # full_path property
    assert slot.full_path == "客厅/U/S/A1"


async def test_storage_slot_section_code_unique(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = await _make_room(db_session, home)
    unit = await _make_unit(db_session, room)
    section = await _make_section(db_session, unit)

    db_session.add(
        StorageSlot(section_id=section.id, code="A1", allowed_categories=["misc"])
    )
    await db_session.flush()
    db_session.add(
        StorageSlot(section_id=section.id, code="A1", allowed_categories=["misc"])
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_cascade_delete_home_to_rooms(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = await _make_room(db_session, home)
    await db_session.flush()

    await db_session.delete(home)
    await db_session.flush()

    remaining = (await db_session.execute(select(Room).where(Room.id == room.id))).scalar_one_or_none()
    assert remaining is None


# --------------------------------------------------------------------------- CHECK constraints


@pytest.mark.parametrize(
    "value",
    [rt.value for rt in RoomType],
)
async def test_room_type_accepts_valid_enum(db_session, value):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = Room(home_id=home.id, name="r", room_type=value)
    db_session.add(room)
    await db_session.flush()  # must not raise


async def test_room_type_rejects_invalid_value(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    db_session.add(Room(home_id=home.id, name="r", room_type="spaceship"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_placement_source_check(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = await _make_room(db_session, home)
    unit = await _make_unit(db_session, room)
    section = await _make_section(db_session, unit)
    slot = await _make_slot(db_session, section)
    item = Item(home_id=home.id, name="mug", created_by=user.id)
    db_session.add(item)
    await db_session.flush()

    db_session.add(
        ItemPlacement(
            item_id=item.id,
            slot_id=slot.id,
            placed_by=user.id,
            source="ufo",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_recommendation_status_check(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    item = Item(home_id=home.id, name="i", created_by=user.id)
    db_session.add(item)
    await db_session.flush()
    trace = AgentTrace(
        user_id=user.id,
        home_id=home.id,
        steps=[],
        final_status="success",
        total_duration_ms=10,
    )
    db_session.add(trace)
    await db_session.flush()
    db_session.add(
        Recommendation(
            item_id=item.id,
            agent_trace_id=trace.id,
            candidates=[],
            status="bogus",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# --------------------------------------------------------------------------- item / placement


async def test_item_placement_active_flag(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    room = await _make_room(db_session, home)
    unit = await _make_unit(db_session, room)
    section = await _make_section(db_session, unit)
    slot = await _make_slot(db_session, section)

    item = Item(home_id=home.id, name="pen", created_by=user.id)
    db_session.add(item)
    await db_session.flush()

    p = ItemPlacement(
        item_id=item.id,
        slot_id=slot.id,
        placed_by=user.id,
        source=PlacementSource.USER_MANUAL.value,
    )
    db_session.add(p)
    await db_session.flush()

    assert p.is_active is True
    p.removed_at = datetime.now(UTC)
    assert p.is_active is False


async def test_agent_trace_steps_json_round_trip(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    steps = [
        {"step": "vision", "ok": True, "data": {"name": "cup"}},
        {"step": "candidate_generation", "count": 5},
    ]
    trace = AgentTrace(
        user_id=user.id,
        home_id=home.id,
        steps=steps,
        final_status="success",
        total_duration_ms=1234,
        llm_tokens_in=100,
        llm_tokens_out=42,
        llm_cost_usd=0.000123,
    )
    db_session.add(trace)
    await db_session.flush()
    await db_session.commit()

    # Re-fetch
    fetched = (
        await db_session.execute(select(AgentTrace).where(AgentTrace.id == trace.id))
    ).scalar_one()
    assert fetched.steps == steps
    assert fetched.llm_cost_usd == pytest.approx(0.000123)


# --------------------------------------------------------------------------- home_rules


async def test_home_rule_hard_vs_soft_check(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)

    rule = HomeRule(
        home_id=home.id,
        name="no glass in kid room",
        description="no glass items in bedroom",
        rule_type=HomeRuleType.HARD.value,
        scope={"room_types": ["bedroom"]},
    )
    db_session.add(rule)
    await db_session.flush()
    assert rule.scope == {"room_types": ["bedroom"]}


async def test_home_rule_rejects_invalid_type(db_session):
    user = await _make_user(db_session)
    home = await _make_home(db_session, user)
    db_session.add(
        HomeRule(
            home_id=home.id,
            name="r",
            description="d",
            rule_type="undecided",
            scope={},
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
