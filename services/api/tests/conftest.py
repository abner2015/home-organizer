"""Shared pytest fixtures.

Two scopes:

1. `db_engine` / `db_session` (function-scoped) — create an in-memory SQLite
   database, build the full schema via `Base.metadata.create_all`, and yield
   a clean session. Used by tests that exercise ORM behavior.

2. `client` — FastAPI TestClient. The Phase 1 health endpoint doesn't need
   the DB; Phase 2 tests that hit the API + DB should depend on both.

For production-flavored integration tests (genuine PG partial-unique indexes,
JSONB operators, etc.) add `tests/integration/` that requires a real Postgres
via the `TEST_DATABASE_URL` env var and is skipped otherwise.
"""
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.enums import HomeRole
from app.main import app

# Importing the models package registers all tables on Base.metadata.
from app.models import (  # noqa: F401
    AgentTrace,
    Conversation,
    Home,
    HomeMembership,
    HomeRule,
    Item,
    ItemImage,
    ItemPlacement,
    Message,
    Recommendation,
    Room,
    StorageSection,
    StorageSlot,
    StorageUnit,
    User,
    UserPreference,
)

# ----------------------------------------------------------------- Seeded actor


@dataclass(slots=True)
class SeededActor:
    """User + home + membership created for the duration of one test."""

    user: User
    home: Home
    user_id: uuid.UUID
    home_id: uuid.UUID

    def headers(self) -> dict[str, str]:
        return {
            "X-User-Id": str(self.user_id),
            "X-Home-Id": str(self.home_id),
        }


@pytest_asyncio.fixture
async def seeded_actor(db_engine) -> AsyncIterator[SeededActor]:
    """Insert a user + home + membership, return an Actor referencing them.

    Lives in the root conftest so both ``tests/unit`` and ``tests/api`` tests
    can request it. Per-test scope; on teardown the schema is dropped and
    recreated so each test starts from a clean slate.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            id=uuid.uuid4(),
            email=f"u-{uuid.uuid4().hex}@example.com",
            display_name="Tester",
            password_hash="x",  # not exercised by tests
        )
        home = Home(
            id=uuid.uuid4(),
            name="Test Home",
            owner_id=user.id,
        )
        session.add_all([user, home])
        await session.flush()
        membership = HomeMembership(
            home_id=home.id,
            user_id=user.id,
            role=HomeRole.OWNER.value,
        )
        session.add(membership)
        await session.commit()
        actor = SeededActor(
            user=user,
            home=home,
            user_id=user.id,
            home_id=home.id,
        )
        yield actor
        async with db_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)


# ----------------------------------------------------------------- DB engine


@pytest_asyncio.fixture
async def db_engine() -> AsyncIterator:
    """Fresh in-memory SQLite engine + schema per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """An AsyncSession bound to the test engine."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Phase 1 / health-only TestClient."""
    with TestClient(app) as c:
        yield c
