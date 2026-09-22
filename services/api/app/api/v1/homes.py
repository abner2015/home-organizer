"""Home + storage-structure read endpoints (Phase 10).

The Phase 8 Web app renders the whole home → room → unit → section → slot
hierarchy, but nothing in the API exposed it: every page that called
``GET /api/v1/homes/{id}/space-tree`` got a 404. This router adds the six read
routes the frontend needs, all phrased as thin wrappers over the already-tested
``app/tools/home_tools`` queries so there is exactly one place that knows how
to walk the hierarchy.

Auth is the shared ``get_actor`` dependency (Bearer JWT + ``X-Home-Id``), the
same one assets / items / recommendations / search use. Every route requires
the caller to be a member of the home in the path; non-members and unknown ids
both 404 so the API never leaks the existence of another home's data.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Actor, ensure_member, get_actor, get_current_user
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models import HomeMembership, User
from app.models.item import Item
from app.models.room import Room
from app.models.rule import HomeRule
from app.schemas.home import (
    HomeView,
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


__all__ = ["rooms_router", "router"]
