"""Home membership business logic (P0.8).

Before this module the only way to enter a home was ``POST /auth/signup`` —
the signup path silently provisions the new user with a "我的家" + OWNER
membership, and that was it. This module exposes the four operations an
owner needs to share their home with somebody else:

- :func:`list_members` — any home member can read the roster.
- :func:`invite_member` — owner only; adds an existing user (matched by
  email) to the home. There is no email/SMTP path in this build, so "invite"
  really means "look up the user, give them a row".
- :func:`change_role` — owner only; promotes / demotes one member. Refuses
  to demote the last owner (would strand the home with no admin).
- :func:`remove_member` — owner only; soft-removes the membership by
  deleting the row. Same last-owner guard.

The :func:`build_member_view` projector lives here too — the ``MemberView``
schema is a public API surface, so the projection is a service-level helper
rather than something each route does in isolation.

Cross-home / unknown-email errors all surface as :class:`NotFoundError`
("404 not 403" — the project convention that hides the existence of other
homes). The one exception is owner-only actions: a caller who *is* a member
of the home but not an owner gets :class:`ForbiddenError`, because
"forbidden" here means "you can see this home but you cannot run this
command on it" — not information disclosure.
"""
from __future__ import annotations

import uuid
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.db.enums import HomeRole
from app.models import HomeMembership, User
from app.schemas.home import MemberView


class MemberRow(NamedTuple):
    """(HomeMembership, User) pair — what every read path needs to build a
    ``MemberView``. Returned as a tuple so the projector stays in this module."""

    membership: HomeMembership
    user: User


def build_member_view(row: MemberRow) -> MemberView:
    """Project a ``(membership, User)`` pair into the public response shape."""
    return MemberView(
        user_id=row.user.id,
        display_name=row.user.display_name,
        email=row.user.email,
        role=row.membership.role_enum,
        joined_at=row.membership.joined_at,
    )


async def _count_owners(db: AsyncSession, *, home_id: uuid.UUID) -> int:
    """How many owner memberships this home has.

    Used by both ``change_role`` and ``remove_member`` to refuse the
    "demote / remove the last owner" move with a 409 — otherwise the home
    would be left with no admin and the API would have no way to recover
    (there is no ``POST /homes`` yet to create a new one from scratch).
    """
    stmt = select(func.count()).select_from(HomeMembership).where(
        HomeMembership.home_id == home_id,
        HomeMembership.role == HomeRole.OWNER.value,
    )
    return int((await db.execute(stmt)).scalar_one())


async def _get_membership(
    db: AsyncSession, *, home_id: uuid.UUID, user_id: uuid.UUID
) -> HomeMembership:
    """Fetch one membership, 404 if it does not exist.

    Used by ``change_role`` and ``remove_member`` so a typo'd ``user_id``
    looks the same as a non-member caller — the project convention.
    """
    stmt = select(HomeMembership).where(
        HomeMembership.home_id == home_id,
        HomeMembership.user_id == user_id,
    )
    membership = (await db.execute(stmt)).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("Member not found")
    return membership


async def list_members(db: AsyncSession, *, home_id: uuid.UUID) -> list[MemberRow]:
    """Every membership in this home, oldest first (matches signup order).

    Joins ``User`` eagerly because the response carries ``display_name`` and
    ``email``; ``HomeMembership.user`` is already ``lazy="joined"`` in the
    model, so this is one query, not N+1.
    """
    stmt = (
        select(HomeMembership)
        .where(HomeMembership.home_id == home_id)
        .order_by(HomeMembership.joined_at)
    )
    memberships = (await db.execute(stmt)).scalars().all()
    return [MemberRow(membership=m, user=m.user) for m in memberships]


async def invite_member(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    email: str,
    role: HomeRole,
) -> MemberRow:
    """Add ``email`` to ``home_id`` with ``role``. The membership role comes
    straight from the body — inviting someone directly as an owner is
    deliberate (covers the "two owners from day one" case) but still goes
    through ``change_role``'s last-owner guard on subsequent demotions.

    - Unknown email → 404 ``not_found`` (the friend has not signed up yet).
    - Already a member → 409 ``conflict`` (unique-index violation).
    """
    normalized = email.strip().lower()
    user_stmt = select(User).where(User.email == normalized)
    user = (await db.execute(user_stmt)).scalar_one_or_none()
    if user is None:
        raise NotFoundError("该邮箱还没注册账号")

    membership = HomeMembership(
        home_id=home_id,
        user_id=user.id,
        role=role.value,
    )
    db.add(membership)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("该用户已是该家的成员") from exc
    await db.commit()
    # Re-read through the relationship so the returned row carries the joined
    # ``User`` (matches ``list_members``' shape).
    return MemberRow(membership=membership, user=user)


async def change_role(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    target_user_id: uuid.UUID,
    new_role: HomeRole,
) -> MemberRow:
    """Promote or demote one member.

    Refuses to demote the last owner (would leave the home without an admin).
    The ``new_role == current_role`` case is a no-op (the unique index
    already guarantees one membership per user; nothing else to enforce).
    """
    membership = await _get_membership(db, home_id=home_id, user_id=target_user_id)
    if membership.role_enum == new_role:
        return MemberRow(membership=membership, user=membership.user)
    if (
        membership.role_enum == HomeRole.OWNER
        and new_role == HomeRole.MEMBER
        and await _count_owners(db, home_id=home_id) <= 1
    ):
        raise ConflictError("至少需要保留一个 owner")
    membership.role = new_role.value
    await db.flush()
    await db.commit()
    return MemberRow(membership=membership, user=membership.user)


async def remove_member(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    target_user_id: uuid.UUID,
) -> MemberRow:
    """Delete one membership and return the row that was deleted.

    Same last-owner guard as :func:`change_role`. The 404 for an unknown
    member comes from :func:`_get_membership`. A caller removing
    themselves is allowed — the guard is on the count, not on whether
    actor == target.

    Returning it (rather than returning ``None``) lets the route surface
    the deleted membership's last snapshot — same pattern as
    ``unplace_item`` returning the closed placement so the caller can
    render ``removed_at`` without a follow-up GET.
    """
    membership = await _get_membership(db, home_id=home_id, user_id=target_user_id)
    if (
        membership.role_enum == HomeRole.OWNER
        and await _count_owners(db, home_id=home_id) <= 1
    ):
        raise ConflictError("至少需要保留一个 owner")
    row = MemberRow(membership=membership, user=membership.user)
    await db.delete(membership)
    await db.flush()
    await db.commit()
    return row


__all__ = [
    "MemberRow",
    "build_member_view",
    "change_role",
    "invite_member",
    "list_members",
    "remove_member",
]
