"""``home_service.create_home_for`` — the single "new home" primitive (P0.9).

Until this module existed, signup was the only caller that ever built a
``Home`` + OWNER ``HomeMembership``, and it did so inline. ``POST /homes`` is
the second caller, so the primitive has to live in a service layer the two
share — otherwise they would drift the moment one of them changed.

These tests assert the *contract* of :func:`create_home_for` from the outside
(no mocking of the DB — the same in-memory SQLite the rest of the unit suite
uses). The companion ``tests/api/test_homes_api.py`` covers the route shape
(201 / 401 / 400 / 422); ``tests/api/test_auth.py:test_signup_provisions_exactly_one_home``
guards the behavioral equivalence between signup's old inline path and the
new ``create_home_for``-driven one.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.enums import HomeRole
from app.models import Home, HomeMembership
from app.models.user import User
from app.services import home_service

pytestmark = pytest.mark.asyncio


async def _make_user(db_engine) -> uuid.UUID:
    """Insert a single ``User`` row and return its id.

    Skips the ``auth_service.signup`` ceremony — this suite is testing the
    Home primitive, not User creation.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            User(
                id=user_id,
                email=f"u-{user_id.hex}@example.com",
                password_hash="x",
                display_name="Tester",
            )
        )
        await session.commit()
    return user_id


async def test_create_home_for_returns_persisted_home_and_membership(
    db_engine,
) -> None:
    """Caller becomes OWNER of a freshly-persisted Home + HomeMembership."""
    owner_id = await _make_user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        home = await home_service.create_home_for(
            db=session, owner_id=owner_id, name="老家"
        )
        await session.commit()

    # The returned Home has the expected identity / ownership.
    assert isinstance(home.id, uuid.UUID)
    assert home.name == "老家"
    assert home.owner_id == owner_id

    # And both rows survive the session — they were flushed, not just held
    # in the identity map.
    async with factory() as session:
        homes = (
            (await session.execute(select(Home).where(Home.id == home.id)))
            .scalars()
            .all()
        )
        assert len(homes) == 1
        assert homes[0].owner_id == owner_id

        memberships = (
            (
                await session.execute(
                    select(HomeMembership).where(HomeMembership.home_id == home.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(memberships) == 1
        assert memberships[0].user_id == owner_id
        assert memberships[0].role == HomeRole.OWNER.value


async def test_create_home_for_strips_whitespace_around_name(db_engine) -> None:
    """Leading / trailing whitespace is normalized before persistence."""
    owner_id = await _make_user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        home = await home_service.create_home_for(
            db=session, owner_id=owner_id, name="  老家  "
        )
        await session.commit()

    assert home.name == "老家"

    async with factory() as session:
        persisted = (
            (await session.execute(select(Home).where(Home.id == home.id)))
            .scalars()
            .one()
        )
        assert persisted.name == "老家"


async def test_create_home_for_defaults_timezone_to_asia_shanghai(
    db_engine,
) -> None:
    """``timezone=None`` falls back to the documented default."""
    owner_id = await _make_user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        home = await home_service.create_home_for(
            db=session, owner_id=owner_id, name="工作室"
        )

    assert home.timezone == "Asia/Shanghai"
    assert home.timezone == home_service.DEFAULT_HOME_TIMEZONE


async def test_create_home_for_accepts_explicit_timezone(db_engine) -> None:
    """A non-default timezone is stored verbatim — no whitelist, no coercion."""
    owner_id = await _make_user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        home = await home_service.create_home_for(
            db=session,
            owner_id=owner_id,
            name="NY apartment",
            timezone="America/New_York",
        )

    assert home.timezone == "America/New_York"


async def test_create_home_for_does_not_create_storage_hierarchy(
    db_engine,
) -> None:
    """A new home is *empty* — no rooms, which transitively means no units,
    sections or slots (the whole storage chain hangs off ``Room``).

    Mirrors the signup-time promise: there is no implicit "starter home"
    content. The user gets to ``/home/setup`` and builds the hierarchy
    explicitly. This test exists so the next refactor cannot silently
    regress that.
    """
    from app.models.room import Room

    owner_id = await _make_user(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        home = await home_service.create_home_for(
            db=session, owner_id=owner_id, name="空家"
        )
        await session.commit()

    async with factory() as session:
        rooms = (
            (await session.execute(select(Room).where(Room.home_id == home.id)))
            .scalars()
            .all()
        )
        assert rooms == [], "create_home_for should not seed any rooms"
