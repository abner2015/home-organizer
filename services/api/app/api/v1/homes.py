"""Home + storage-structure read endpoints (Phase 10) + member
management (P0.8).

Phase 10 added the six read routes the Web app needs to render the
home → room → unit → section → slot hierarchy; they are thin wrappers over
the already-tested ``app/tools/home_tools`` queries so there is exactly one
place that knows how to walk the hierarchy.

P0.8 added four member-management routes (``/homes/{id}/members`` family).
They live on the same router because a Home *is* the membership boundary —
splitting them onto a separate router would just shuffle the imports around
without changing the URL surface. Business logic is in
:mod:`app.services.membership_service`; the routes here are projections and
guards. The one extra guard is :func:`_ensure_owner`, the only place the
project allows ``ForbiddenError`` (403) inside a home — non-owner callers
*are* members, so 404 would leak the existence of the home.

Auth is the shared ``get_actor`` dependency (Bearer JWT + ``X-Home-Id``), the
same one assets / items / recommendations / search use. Every read route
requires the caller to be a member of the home in the path; non-members and
unknown ids both 404 so the API never leaks the existence of another home's
data. Management routes first ensure membership (404 otherwise), then ensure
ownership (403 if member-but-not-owner).
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, ensure_member, get_actor, get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.enums import HomeRole
from app.db.session import get_db
from app.models import HomeMembership, User
from app.models.item import Item
from app.models.room import Room
from app.models.rule import HomeRule
from app.schemas.home import (
    HomeView,
    MemberInviteRequest,
    MemberUpdateRequest,
    MemberView,
    RoomTreeView,
    RoomView,
    SpaceTreeView,
    StorageSectionView,
    StorageSlotView,
    StorageUnitView,
    room_view,
    section_view,
    slot_view,
    unit_view,
)
from app.services.membership_service import (
    build_member_view,
    change_role,
    invite_member,
    list_members,
    remove_member,
)
from app.tools.home_tools import (
    get_home,
    get_rooms,
    get_storage_sections,
    get_storage_slots,
    get_storage_units,
)

router = APIRouter(prefix="/homes", tags=["homes"])
rooms_router = APIRouter(prefix="/rooms", tags=["homes"])


# --------------------------------------------------------------------- guards


async def _count(db: AsyncSession, model: Any, home_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.home_id == home_id)
    return int((await db.execute(stmt)).scalar_one())


# ------------------------------------------------------------------ projections


def _home_view(data: dict[str, Any], **counts: int | None) -> HomeView:
    return HomeView(
        id=data["id"],
        name=data["name"],
        timezone=data.get("timezone"),
        owner_id=data.get("owner_id"),
        **counts,
    )


async def _nested_units(
    db: AsyncSession, *, home_id: uuid.UUID, room_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """Build the section/slot-nested unit views as plain dicts.

    Returned as dicts (not Pydantic models) so callers can attach them to a
    ``RoomTreeView`` without re-validating the whole tree twice.
    """
    units = await get_storage_units(db=db, home_id=home_id, room_id=room_id)
    sections = await get_storage_sections(db=db, home_id=home_id)
    slots = await get_storage_slots(db=db, home_id=home_id)

    slots_by_section: dict[str, list[StorageSlotView]] = defaultdict(list)
    for slot in slots:
        slots_by_section[slot["section_id"]].append(slot_view(slot))

    sections_by_unit: dict[str, list[StorageSectionView]] = defaultdict(list)
    for section in sections:
        sections_by_unit[section["unit_id"]].append(
            section_view(section, slots=slots_by_section[section["id"]])
        )

    out: list[dict[str, Any]] = []
    for unit in units:
        out.append(
            unit_view(unit, sections=sections_by_unit[unit["id"]]).model_dump(mode="json")
        )
    return out


async def _space_tree(db: AsyncSession, *, home_id: uuid.UUID) -> SpaceTreeView:
    home = await get_home(db=db, home_id=home_id)
    rooms = await get_rooms(db=db, home_id=home_id)
    units = await _nested_units(db, home_id=home_id)

    units_by_room: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for unit in units:
        units_by_room[unit["room_id"]].append(unit)

    room_trees = [
        RoomTreeView(
            **room_view(room, unit_count=len(units_by_room[room["id"]])).model_dump(),
            units=units_by_room[room["id"]],
        )
        for room in rooms
    ]
    return SpaceTreeView(home=_home_view(home), rooms=room_trees)


# ---------------------------------------------------------------------- routes


@router.get("", response_model=list[HomeView], summary="List the caller's homes")
async def list_homes(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[HomeView]:
    """Homes the caller is a member of, oldest first.

    Uses ``get_current_user`` rather than ``get_actor``: listing your homes does
    not presuppose *which* one you are acting in, so there is no ``X-Home-Id``
    to verify and no reason to require one.
    """
    stmt = (
        select(HomeMembership)
        .where(HomeMembership.user_id == current_user.id)
        .order_by(HomeMembership.joined_at)
    )
    memberships = (await db.execute(stmt)).scalars().all()
    views: list[HomeView] = []
    for membership in memberships:
        home = membership.home
        views.append(
            _home_view(
                {
                    "id": str(home.id),
                    "name": home.name,
                    "timezone": home.timezone,
                    "owner_id": str(home.owner_id),
                },
                member_count=await _count(db, HomeMembership, home.id),
                item_count=await _count(db, Item, home.id),
                rule_count=await _count(db, HomeRule, home.id),
            )
        )
    return views


@router.get("/{home_id}", response_model=HomeView, summary="Fetch one home")
async def get_home_endpoint(
    home_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HomeView:
    """Single home with its member / item / rule counts. Non-member → 404."""
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    home = await get_home(db=db, home_id=home_id)
    return _home_view(
        home,
        member_count=await _count(db, HomeMembership, home_id),
        item_count=await _count(db, Item, home_id),
        rule_count=await _count(db, HomeRule, home_id),
    )


@router.get(
    "/{home_id}/rooms",
    response_model=list[RoomView],
    summary="List a home's rooms",
)
async def list_rooms(
    home_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[RoomView]:
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    rooms = await get_rooms(db=db, home_id=home_id)
    units = await get_storage_units(db=db, home_id=home_id)
    unit_counts: dict[str, int] = defaultdict(int)
    for unit in units:
        unit_counts[unit["room_id"]] += 1
    return [room_view(room, unit_count=unit_counts[room["id"]]) for room in rooms]


@router.get(
    "/{home_id}/space-tree",
    response_model=SpaceTreeView,
    summary="Full room → unit → section → slot hierarchy",
)
async def get_space_tree(
    home_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SpaceTreeView:
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    return await _space_tree(db, home_id=home_id)


@router.get(
    "/{home_id}/slots",
    response_model=list[StorageSlotView],
    summary="Flat list of every slot in a home",
)
async def list_slots(
    home_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[StorageSlotView]:
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    slots = await get_storage_slots(db=db, home_id=home_id)
    return [slot_view(slot) for slot in slots]


@rooms_router.get(
    "/{room_id}/storage-units",
    response_model=list[StorageUnitView],
    summary="A room's storage units with nested sections and slots",
)
async def list_storage_units(
    room_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[StorageUnitView]:
    room = (
        await db.execute(select(Room).where(Room.id == room_id))
    ).scalar_one_or_none()
    if room is None or room.home_id != actor.home_id:
        raise NotFoundError("Room not found")
    return [StorageUnitView(**unit) for unit in await _nested_units(
        db, home_id=room.home_id, room_id=room_id
    )]


# --------------------------------------------------------------------- P0.8
# Member management
#
# All four routes share the same call graph: ensure membership (404 if not),
# then for management-class actions ensure ownership (403 if not), then
# delegate to ``membership_service``. The split exists so the routes are
# thin and so the business logic — including the last-owner guard — stays
# in one place that's easy to test in isolation.


async def _ensure_owner(db: AsyncSession, *, home_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Raise ``ForbiddenError`` unless ``user_id`` is an OWNER of ``home_id``.

    Caller is expected to have already passed ``ensure_member``; this checks
    the role. This is one of two places in the project where 403 is
    legitimate (the other is ``PATCH /homes/{id}`` non-owner); everywhere
    else prefers 404 not-403 to avoid leaking "this home exists and you are
    not in it".
    """
    stmt = select(HomeMembership.role).where(
        HomeMembership.home_id == home_id,
        HomeMembership.user_id == user_id,
    )
    role = (await db.execute(stmt)).scalar_one_or_none()
    if role != HomeRole.OWNER.value:
        raise ForbiddenError("只有 owner 可以管理成员")


@router.get(
    "/{home_id}/members",
    response_model=list[MemberView],
    summary="List a home's members",
)
async def list_home_members(
    home_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[MemberView]:
    """Every member of this home, oldest first (signup order).

    Open to any member — owners, members, and self alike — so people can
    see who else shares the home with them. Returns 404 for non-members
    (not 403), per project convention.
    """
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    rows = await list_members(db, home_id=home_id)
    return [build_member_view(row) for row in rows]


@router.post(
    "/{home_id}/members",
    response_model=MemberView,
    status_code=status.HTTP_200_OK,
    summary="Add an existing user (matched by email) to this home",
)
async def invite_home_member(
    home_id: uuid.UUID,
    body: MemberInviteRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemberView:
    """Invite by email. Owner only.

    - email matches an existing ``User`` → 200 + the new member view.
    - email does not match anyone → 404 not_found「该邮箱还没注册账号」.
      There is no email/SMTP path in this build, so the friend has to
      register first; the call *is* the invite.
    - user is already a member → 409 conflict.

    Returns 200 (not 201) so the same shape works for re-adding after a
    previous remove — the resource already existed before this request.
    """
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    await _ensure_owner(db, home_id=home_id, user_id=actor.user_id)
    row = await invite_member(
        db,
        home_id=home_id,
        email=body.email,
        role=body.role,
    )
    return build_member_view(row)


@router.patch(
    "/{home_id}/members/{user_id}",
    response_model=MemberView,
    summary="Change a member's role",
)
async def update_home_member(
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemberView:
    """Promote / demote one member. Owner only.

    - Demoting the last remaining owner → 409 conflict「至少需要保留一个 owner」.
    - Promoting a member to owner is allowed (covers the "two owners from
      day one" case where the original owner invited a co-owner directly).
    - Setting the same role they already have is a no-op (still returns
      the member view).
    """
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    await _ensure_owner(db, home_id=home_id, user_id=actor.user_id)
    row = await change_role(
        db,
        home_id=home_id,
        target_user_id=user_id,
        new_role=body.role,
    )
    return build_member_view(row)


@router.delete(
    "/{home_id}/members/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove a member from this home",
)
async def delete_home_member(
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MemberView:
    """Owner only. Removing yourself is allowed; removing the last owner is
    not (409). Unknown user_id → 404 not_found.

    The row is hard-deleted — there is no soft-delete on memberships the
    way placements have ``removed_at``, because a re-``POST`` after a
    remove is just a new invite with no audit trail worth carrying. (Items
    the ex-member had placed keep their ``placed_by_user_id`` history
    unchanged — that is the audit trail that matters.)

    Returns the last snapshot of the removed membership (200, not 204) so
    the caller can render what was deleted without a follow-up GET.
    """
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    await _ensure_owner(db, home_id=home_id, user_id=actor.user_id)
    row = await remove_member(db, home_id=home_id, target_user_id=user_id)
    return build_member_view(row)


__all__ = ["rooms_router", "router"]
