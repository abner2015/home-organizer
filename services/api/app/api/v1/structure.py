"""Write endpoints for the home's storage structure.

Before this router the hierarchy could only be built by running
``python -m app.db.seed``: a newly registered account owned a home with no
rooms, ``POST /recommendations`` therefore always answered
``state="failed"`` with 「无符合硬规则的位置」, and there was nothing the user
could do about it. These five routers are the missing half of the read routes
in :mod:`app.api.v1.homes`.

Deliberately a separate module rather than an extension of ``homes.py``: the
write side needs five URL prefixes, three of which (``/storage-units``,
``/sections``, ``/slots``) did not exist anywhere in the codebase. It also
keeps ``tests/api/test_homes_api.py`` — 26 tests of pure read behaviour —
byte-identical, so a regression here cannot hide behind an unrelated one.

Auth is the shared ``get_actor`` dependency, so a non-member of the home named
in the path gets a 404 (never a 403) for exactly the same reason as the read
routes. ``PATCH /homes/{id}`` is the one place a 403 is correct: the caller
*is* a member, they just are not the owner, and that fact is already visible
to them.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, ensure_member, get_actor
from app.core.exceptions import ForbiddenError, NotFoundError
from app.db.enums import HomeRole
from app.db.session import get_db
from app.models import HomeMembership
from app.models.home import Home
from app.schemas.home import (
    HomeView,
    RoomView,
    StorageSectionView,
    StorageSlotView,
    StorageUnitView,
    room_view,
    section_view,
    slot_view,
    unit_view,
)
from app.schemas.structure import (
    HomeUpdateRequest,
    RoomCreateRequest,
    RoomUpdateRequest,
    SectionCreateRequest,
    SectionUpdateRequest,
    SlotCreateRequest,
    SlotUpdateRequest,
    StructureProposalRequest,
    StructureProposalResponse,
    UnitCreateRequest,
    UnitUpdateRequest,
)
from app.services import structure_proposal_service, structure_service
from app.services.projection import (
    ROOM_FIELDS,
    SECTION_FIELDS,
    SLOT_FIELDS,
    UNIT_FIELDS,
    room_view_with_units,
    row_dict,
    section_view_with_slots,
    slot_view_with_enrichment,
    unit_view_with_sections,
)

router = APIRouter(prefix="/homes", tags=["structure"])
rooms_router = APIRouter(prefix="/rooms", tags=["structure"])
units_router = APIRouter(prefix="/storage-units", tags=["structure"])
sections_router = APIRouter(prefix="/sections", tags=["structure"])
slots_router = APIRouter(prefix="/slots", tags=["structure"])
structures_router = APIRouter(prefix="/structures", tags=["structure"])


def _get_ai_provider() -> AIProvider:
    """FastAPI dependency. Overridden in tests to inject a mock."""
    return get_provider()


# ------------------------------------------------------------------------ homes


@router.patch(
    "/{home_id}",
    response_model=HomeView,
    summary="Rename a home (owner only)",
)
async def rename_home(
    home_id: uuid.UUID,
    payload: HomeUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> HomeView:
    """Change a home's name.

    The only write of the P0.2 batch that is not merely membership-gated: the
    name is on every member's screen, so it belongs to the owner. Non-members
    have already been 404'd by ``get_actor``; this 403 is for someone who is
    in the home but is not its owner.
    """
    await _require_owner(db, home_id=home_id, user_id=actor.user_id)
    home = (
        await db.execute(select(Home).where(Home.id == home_id))
    ).scalar_one_or_none()
    if home is None:  # pragma: no cover - unreachable while a membership exists
        raise NotFoundError("Home not found")
    home.name = payload.name
    await db.commit()
    return HomeView(
        id=home.id, name=home.name, timezone=home.timezone, owner_id=home.owner_id
    )


async def _require_owner(
    db: AsyncSession, *, home_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    role = (
        await db.execute(
            select(HomeMembership.role).where(
                HomeMembership.home_id == home_id,
                HomeMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if role is None:
        raise NotFoundError("Home not found")
    if role != HomeRole.OWNER:
        raise ForbiddenError("只有家庭管理员可以修改家庭名称")


# ------------------------------------------------------------------------ rooms


@router.post(
    "/{home_id}/rooms",
    response_model=RoomView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a room in a home",
)
async def create_room(
    home_id: uuid.UUID,
    payload: RoomCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoomView:
    """Add a room. This is where a new home's first room comes from."""
    await ensure_member(db, home_id=home_id, user_id=actor.user_id)
    room = await structure_service.create_room(
        db,
        home_id=home_id,
        name=payload.name,
        room_type=str(payload.room_type),
        sort_order=payload.sort_order,
    )
    await db.commit()
    await db.refresh(room)
    return room_view(row_dict(room, ROOM_FIELDS), unit_count=0)


@rooms_router.patch(
    "/{room_id}", response_model=RoomView, summary="Update a room"
)
async def update_room(
    room_id: uuid.UUID,
    payload: RoomUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RoomView:
    changes = _enum_strings(payload.changes())
    room = await structure_service.update_room(
        db, room_id=room_id, home_id=actor.home_id, changes=changes
    )
    await db.commit()
    await db.refresh(room)
    return await room_view_with_units(db, home_id=actor.home_id, room=room)


@rooms_router.delete(
    "/{room_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty room",
)
async def delete_room(
    room_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """409 while the room still holds storage units."""
    await structure_service.delete_room(
        db, room_id=room_id, home_id=actor.home_id
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ----------------------------------------------------------------- storage units


@rooms_router.post(
    "/{room_id}/storage-units",
    response_model=StorageUnitView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a storage unit in a room",
)
async def create_unit(
    room_id: uuid.UUID,
    payload: UnitCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageUnitView:
    unit = await structure_service.create_unit(
        db,
        home_id=actor.home_id,
        room_id=room_id,
        name=payload.name,
        unit_type=str(payload.unit_type),
        description=payload.description,
        sort_order=payload.sort_order,
    )
    await db.commit()
    await db.refresh(unit)
    return unit_view(row_dict(unit, UNIT_FIELDS), sections=[])


@units_router.patch(
    "/{unit_id}", response_model=StorageUnitView, summary="Update a storage unit"
)
async def update_unit(
    unit_id: uuid.UUID,
    payload: UnitUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageUnitView:
    changes = _enum_strings(payload.changes())
    unit = await structure_service.update_unit(
        db, unit_id=unit_id, home_id=actor.home_id, changes=changes
    )
    await db.commit()
    await db.refresh(unit)
    return await unit_view_with_sections(db, home_id=actor.home_id, unit=unit)


@units_router.delete(
    "/{unit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty storage unit",
)
async def delete_unit(
    unit_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """409 while the unit still holds sections."""
    await structure_service.delete_unit(
        db, unit_id=unit_id, home_id=actor.home_id
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- sections


@units_router.post(
    "/{unit_id}/sections",
    response_model=StorageSectionView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a section in a storage unit",
)
async def create_section(
    unit_id: uuid.UUID,
    payload: SectionCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageSectionView:
    section = await structure_service.create_section(
        db,
        home_id=actor.home_id,
        unit_id=unit_id,
        name=payload.name,
        section_type=str(payload.section_type),
        sort_order=payload.sort_order,
    )
    await db.commit()
    await db.refresh(section)
    return section_view(row_dict(section, SECTION_FIELDS), slots=[])


@sections_router.patch(
    "/{section_id}", response_model=StorageSectionView, summary="Update a section"
)
async def update_section(
    section_id: uuid.UUID,
    payload: SectionUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageSectionView:
    changes = _enum_strings(payload.changes())
    section = await structure_service.update_section(
        db, section_id=section_id, home_id=actor.home_id, changes=changes
    )
    await db.commit()
    await db.refresh(section)
    return await section_view_with_slots(db, home_id=actor.home_id, section=section)


@sections_router.delete(
    "/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an empty section",
)
async def delete_section(
    section_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """409 while the section still holds slots."""
    await structure_service.delete_section(
        db, section_id=section_id, home_id=actor.home_id
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------------ slots


@sections_router.post(
    "/{section_id}/slots",
    response_model=StorageSlotView,
    status_code=status.HTTP_201_CREATED,
    summary="Create a slot in a section",
)
async def create_slot(
    section_id: uuid.UUID,
    payload: SlotCreateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageSlotView:
    """The leaf level — the thing the recommendation agent ultimately picks."""
    slot = await structure_service.create_slot(
        db,
        home_id=actor.home_id,
        section_id=section_id,
        code=payload.code,
        label=payload.label,
        capacity_hint=payload.capacity_hint,
        allowed_categories=payload.allowed_categories,
        sort_order=payload.sort_order,
    )
    await db.commit()
    await db.refresh(slot)
    return slot_view({**row_dict(slot, SLOT_FIELDS), "active_count": 0})


@slots_router.patch(
    "/{slot_id}", response_model=StorageSlotView, summary="Update a slot"
)
async def update_slot(
    slot_id: uuid.UUID,
    payload: SlotUpdateRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StorageSlotView:
    slot = await structure_service.update_slot(
        db, slot_id=slot_id, home_id=actor.home_id, changes=payload.changes()
    )
    await db.commit()
    await db.refresh(slot)
    return await slot_view_with_enrichment(db, home_id=actor.home_id, slot=slot)


@slots_router.delete(
    "/{slot_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a slot that has never held an item",
)
async def delete_slot(
    slot_id: uuid.UUID,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    """409 while *any* placement record exists — including removed ones."""
    await structure_service.delete_slot(
        db, slot_id=slot_id, home_id=actor.home_id
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ proposals


@structures_router.post(
    "/propose",
    response_model=StructureProposalResponse,
    summary="Propose a storage structure from a photo, a sentence, or nothing",
)
async def propose_structure(
    payload: StructureProposalRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> StructureProposalResponse:
    """Run Step 1-5 of ``docs/AGENT.md`` §14 and return the proposal.

    Nothing is written except an ``AgentTrace`` row: the four storage tables
    are untouched, and the client persists the parts the user kept through the
    write routes above. A request with neither ``asset_id`` nor a
    ``description`` returns the server-side template without calling a model.

    Errors follow the vision path's taxonomy — 404 for an asset outside the
    caller's home, 503 for provider failures, 503 after parse retries are
    exhausted.
    """
    result = await structure_proposal_service.propose_structure(
        db,
        provider=provider,
        home_id=actor.home_id,
        user_id=actor.user_id,
        asset_id=payload.asset_id,
        description=payload.description,
    )
    await db.commit()
    return StructureProposalResponse(
        proposal=result.proposal,
        warnings=result.warnings,
        source=result.source,
        trace_id=result.trace_id,
    )


def _enum_strings(changes: dict[str, object]) -> dict[str, object]:
    """Coerce StrEnum members to plain ``str`` before they reach the column.

    Pydantic parses ``room_type`` into ``RoomType`` so an invalid value is a
    422; the ``Text`` column wants the raw value back.
    """
    return {
        key: (str(value) if isinstance(value, str) else value)
        for key, value in changes.items()
    }


__all__ = [
    "rooms_router",
    "router",
    "sections_router",
    "slots_router",
    "structures_router",
    "units_router",
]
