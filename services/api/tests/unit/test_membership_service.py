"""Membership service — list / invite / change-role / remove (P0.8).

Unit-level tests using the real SQLite DB + the real service layer. Covers
the business invariants:

- invite matches the email against ``User``; unknown email → 404.
- invite reuses an existing membership → 409 (unique index).
- change_role refuses to demote the last remaining owner → 409.
- remove refuses to remove the last remaining owner → 409.
- a same-role change is a no-op (no DB write, no 409).

Cross-cutting checks that live in the routes' `_ensure_member` /
`ensure_member` (not the service) are covered by the API tests; here we
exercise the business invariants in isolation.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import ConflictError, NotFoundError
from app.db.enums import HomeRole
from app.models import HomeMembership
from app.models import User as UserModel
from app.services.membership_service import (
    build_member_view,
    change_role,
    invite_member,
    list_members,
    remove_member,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------- helpers


@dataclass(slots=True)
class _ExtraUser:
    """A user inserted directly into the DB, by email."""

    user_id: uuid.UUID
    email: str
    display_name: str


async def _make_user(
    db_engine,
    *,
    email: str | None = None,
    display_name: str = "Friend",
) -> _ExtraUser:
    """Insert a second ``User`` row that can be looked up by email.

    Normalizes the email the same way ``auth_service.signup`` does — lower-
    case + strip — so the lookup mirrors production: a real account is
    always created with a normalized email, and the invite logic relies on
    that invariant.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    raw_email = email or f"friend-{uuid.uuid4().hex}@example.com"
    normalized = raw_email.strip().lower()
    user_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            UserModel(
                id=user_id,
                email=normalized,
                password_hash="x",
                display_name=display_name,
            )
        )
        await session.commit()
    return _ExtraUser(user_id=user_id, email=normalized, display_name=display_name)


def _session(db_engine):  # type: ignore[no-untyped-def]
    """Open a fresh async session bound to the test engine."""
    return _SessionHolder(db_engine)


class _SessionHolder:
    def __init__(self, engine) -> None:  # type: ignore[no-untyped-def]
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        self._sess = self._factory()
        return self._sess

    async def __aexit__(self, *exc: object) -> None:
        await self._sess.close()


# --------------------------------------------------------------- list


async def test_list_members_includes_owner(seeded_actor, db_engine) -> None:
    """The seeded actor (owner) is the only member on a fresh house."""
    async with _session(db_engine) as session:
        rows = await list_members(db=session, home_id=seeded_actor.home_id)
    assert len(rows) == 1
    assert rows[0].user.id == seeded_actor.user_id
    assert rows[0].membership.role_enum == HomeRole.OWNER


# --------------------------------------------------------------- invite


async def test_invite_existing_user_creates_membership(
    seeded_actor, db_engine
) -> None:
    """An email matching an existing ``User`` adds them to the home."""
    friend = await _make_user(db_engine, display_name="Friend")

    async with _session(db_engine) as session:
        row = await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )

    # The projection must carry the joined ``User`` fields and the chosen role.
    view = build_member_view(row)
    assert view.user_id == friend.user_id
    assert view.email == friend.email
    assert view.display_name == "Friend"
    assert view.role == HomeRole.MEMBER

    # The row is persisted.
    async with _session(db_engine) as session:
        rows = await list_members(db=session, home_id=seeded_actor.home_id)
    assert len(rows) == 2
    assert any(r.user.id == friend.user_id for r in rows)


async def test_invite_unknown_email_is_404(seeded_actor, db_engine) -> None:
    """No ``User`` matches the email → 404 with the documented Chinese message."""
    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError) as exc_info:
            await invite_member(
                db=session,
                home_id=seeded_actor.home_id,
                email="nobody@example.com",
                role=HomeRole.MEMBER,
            )
    assert "还没注册账号" in exc_info.value.message


async def test_invite_already_member_is_409(seeded_actor, db_engine) -> None:
    """Adding a user who is already a member → 409 (unique-index hit)."""
    friend = await _make_user(db_engine)

    async with _session(db_engine) as session:
        await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await invite_member(
                db=session,
                home_id=seeded_actor.home_id,
                email=friend.email,
                role=HomeRole.MEMBER,
            )


async def test_invite_email_is_normalized(seeded_actor, db_engine) -> None:
    """Email is matched case-insensitively and trimmed (matches signup)."""
    raw = f"mixed-{uuid.uuid4().hex}@Example.com"
    friend = await _make_user(db_engine, email=raw)

    async with _session(db_engine) as session:
        row = await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            # Uppercase + leading whitespace — signup's `_normalize_email`
            # handles both; the user was stored normalized by `_make_user`.
            email=f"  {friend.email.upper()}  ",
            role=HomeRole.MEMBER,
        )
    assert row.user.id == friend.user_id


# --------------------------------------------------------------- change_role


async def test_change_role_owner_to_member(seeded_actor, db_engine) -> None:
    """Promote a friend to owner (two owners), then demote one — no 409."""
    friend = await _make_user(db_engine)

    # Add the friend as MEMBER.
    async with _session(db_engine) as session:
        await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )

    # Promote MEMBER → OWNER (this is the "two owners from day one" case).
    async with _session(db_engine) as session:
        promoted = await change_role(
            db=session,
            home_id=seeded_actor.home_id,
            target_user_id=friend.user_id,
            new_role=HomeRole.OWNER,
        )
    assert promoted.membership.role_enum == HomeRole.OWNER

    # Demote one of the two owners — fine, the other still has it.
    async with _session(db_engine) as session:
        demoted = await change_role(
            db=session,
            home_id=seeded_actor.home_id,
            target_user_id=friend.user_id,
            new_role=HomeRole.MEMBER,
        )
    assert demoted.membership.role_enum == HomeRole.MEMBER


async def test_demote_last_owner_is_409(seeded_actor, db_engine) -> None:
    """The seeded actor is the only owner — trying to demote them → 409."""
    async with _session(db_engine) as session:
        with pytest.raises(ConflictError) as exc_info:
            await change_role(
                db=session,
                home_id=seeded_actor.home_id,
                target_user_id=seeded_actor.user_id,
                new_role=HomeRole.MEMBER,
            )
    assert "owner" in exc_info.value.message

    # The role was not actually changed.
    async with _session(db_engine) as session:
        rows = await list_members(db=session, home_id=seeded_actor.home_id)
    assert rows[0].membership.role_enum == HomeRole.OWNER


async def test_change_role_same_role_is_noop(seeded_actor, db_engine) -> None:
    """Setting the role to what it already is → returns the row, no write."""
    async with _session(db_engine) as session:
        row = await change_role(
            db=session,
            home_id=seeded_actor.home_id,
            target_user_id=seeded_actor.user_id,
            new_role=HomeRole.OWNER,
        )
    assert row.membership.role_enum == HomeRole.OWNER


async def test_change_role_unknown_member_is_404(seeded_actor, db_engine) -> None:
    """Demoting a user who is not a member of this home → 404."""
    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await change_role(
                db=session,
                home_id=seeded_actor.home_id,
                target_user_id=uuid.uuid4(),
                new_role=HomeRole.MEMBER,
            )


# --------------------------------------------------------------- remove


async def test_remove_member_happy_path(seeded_actor, db_engine) -> None:
    """A member can be removed by id; the returned row is the one deleted."""
    friend = await _make_user(db_engine)

    async with _session(db_engine) as session:
        await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )

    async with _session(db_engine) as session:
        removed = await remove_member(
            db=session,
            home_id=seeded_actor.home_id,
            target_user_id=friend.user_id,
        )
    assert removed.user.id == friend.user_id
    assert removed.membership.role_enum == HomeRole.MEMBER

    # The row is gone — only the owner remains.
    async with _session(db_engine) as session:
        rows = await list_members(db=session, home_id=seeded_actor.home_id)
    assert len(rows) == 1
    assert rows[0].user.id == seeded_actor.user_id


async def test_remove_last_owner_is_409(seeded_actor, db_engine) -> None:
    """The seeded actor is the only owner; trying to remove them → 409."""
    async with _session(db_engine) as session:
        with pytest.raises(ConflictError) as exc_info:
            await remove_member(
                db=session,
                home_id=seeded_actor.home_id,
                target_user_id=seeded_actor.user_id,
            )
    assert "owner" in exc_info.value.message

    # The membership still exists.
    async with _session(db_engine) as session:
        rows = await list_members(db=session, home_id=seeded_actor.home_id)
    assert len(rows) == 1


async def test_remove_unknown_member_is_404(seeded_actor, db_engine) -> None:
    """Removing a user who is not a member → 404 (project convention)."""
    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await remove_member(
                db=session,
                home_id=seeded_actor.home_id,
                target_user_id=uuid.uuid4(),
            )


async def test_remove_then_reinvite_works(seeded_actor, db_engine) -> None:
    """Remove is a hard delete (not soft), so re-inviting the same user works."""
    friend = await _make_user(db_engine)

    async with _session(db_engine) as session:
        await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )
        await remove_member(
            db=session,
            home_id=seeded_actor.home_id,
            target_user_id=friend.user_id,
        )

    async with _session(db_engine) as session:
        # Re-invite — no IntegrityError this time.
        await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )


# --------------------------------------------------------------- invariants


async def test_build_member_view_round_trip(seeded_actor, db_engine) -> None:
    """The projector hands back a ``MemberView`` with the right enum value."""
    friend = await _make_user(db_engine, display_name="Bob")

    async with _session(db_engine) as session:
        row = await invite_member(
            db=session,
            home_id=seeded_actor.home_id,
            email=friend.email,
            role=HomeRole.MEMBER,
        )

    view = build_member_view(row)
    # Enum-typed field, not the raw column value.
    assert view.role is HomeRole.MEMBER
    assert view.role != "owner"


# Silence unused-import warning while keeping the symbol handy for follow-up
# tests that might want to assert on the row directly.
_ = HomeMembership
