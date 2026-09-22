"""``record_preferred_slot`` — the single positive-feedback writer (P0.4).

The unique index ``uq_user_preferences_user_home_key`` is a *plain* unique
index, so SQLite enforces it just like Postgres: a blind insert for a second
accept would raise ``IntegrityError``. These tests pin the select-then-update
behaviour and the "flush, don't commit" contract the accept path relies on.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Item
from app.models.preference import UserPreference
from app.tools.write_tools import PREFERENCE_KEY_PREFERRED_SLOTS, record_preferred_slot

pytestmark = pytest.mark.asyncio


def _factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _rows(session) -> list[UserPreference]:
    return list(
        (
            await session.execute(
                select(UserPreference).where(
                    UserPreference.key == PREFERENCE_KEY_PREFERRED_SLOTS
                )
            )
        ).scalars()
    )


async def test_repeated_calls_upsert_into_one_row_and_bump_count(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    slot_id = storage_hierarchy.slots["L1S1"]
    item_id = storage_hierarchy.items["马克杯"]
    session = _factory(db_engine)()
    try:
        for _ in range(2):
            await record_preferred_slot(
                db=session,
                user_id=seeded_actor.user_id,
                home_id=seeded_actor.home_id,
                item_id=item_id,
                slot_id=slot_id,
            )
        await session.commit()
        rows = await _rows(session)
        assert len(rows) == 1
        slots = rows[0].value["slots"]
        assert slots[str(slot_id)]["count"] == 2
        assert slots[str(slot_id)]["category"] == "utensil"
    finally:
        await session.close()


async def test_stored_category_is_lowercased(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    session = _factory(db_engine)()
    try:
        item = (
            await session.execute(
                select(Item).where(Item.id == storage_hierarchy.items["马克杯"])
            )
        ).scalar_one()
        item.category = "Utensil"
        await session.flush()
        await record_preferred_slot(
            db=session,
            user_id=seeded_actor.user_id,
            home_id=seeded_actor.home_id,
            item_id=item.id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        await session.commit()
        rows = await _rows(session)
        assert rows[0].value["slots"][str(storage_hierarchy.slots["L1S1"])][
            "category"
        ] == "utensil"
    finally:
        await session.close()


async def test_flush_only_rollback_leaves_no_row(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """``record_preferred_slot`` must NOT commit — the accept path owns the
    transaction so placement + status + preference land atomically."""
    session = _factory(db_engine)()
    try:
        await record_preferred_slot(
            db=session,
            user_id=seeded_actor.user_id,
            home_id=seeded_actor.home_id,
            item_id=storage_hierarchy.items["马克杯"],
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        await session.rollback()
        assert await _rows(session) == []
    finally:
        await session.close()


async def test_item_without_a_category_stores_an_empty_one(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A null category must not blow up; the preference just matches any item."""
    session = _factory(db_engine)()
    try:
        item = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="无类别物品",
            category=None,
            created_by=seeded_actor.user_id,
        )
        session.add(item)
        await session.flush()
        await record_preferred_slot(
            db=session,
            user_id=seeded_actor.user_id,
            home_id=seeded_actor.home_id,
            item_id=item.id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        await session.commit()
        rows = await _rows(session)
        assert rows[0].value["slots"][str(storage_hierarchy.slots["L1S1"])][
            "category"
        ] == ""
    finally:
        await session.close()
