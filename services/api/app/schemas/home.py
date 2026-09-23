"""Pydantic schemas for the home / storage-structure read API.

These mirror the shapes the Web app's ``src/lib/types.ts`` declares, so the
``/home``, ``/home/rooms`` and ``/home/storage`` pages can render the seeded
hierarchy directly. Like every response model in this package they are
``extra="forbid"`` (hygiene) without ``strict`` — the payloads are built from
JSON-safe dicts whose UUIDs are strings.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.db.enums import HomeRole


class HomeView(BaseModel):
    """A home. ``member_count`` / ``item_count`` / ``rule_count`` are only
    filled by the single-home endpoint (the tree endpoint skips the extra
    counts — nothing on those pages renders them)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    timezone: str | None = None
    owner_id: uuid.UUID | None = None
    member_count: int | None = None
    item_count: int | None = None
    rule_count: int | None = None


class RoomView(BaseModel):
    """A room inside a home."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    home_id: uuid.UUID
    name: str
    room_type: str
    sort_order: int = 0
    unit_count: int = 0


class StorageSlotView(BaseModel):
    """A leaf storage position."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    section_id: uuid.UUID
    code: str
    label: str | None = None
    capacity_hint: str | None = None
    allowed_categories: list[str] = Field(default_factory=list)
    sort_order: int = 0
    active_count: int = 0


class StorageSectionView(BaseModel):
    """A layer / drawer / compartment inside a storage unit."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    unit_id: uuid.UUID
    name: str
    section_type: str
    sort_order: int = 0
    slots: list[StorageSlotView] = Field(default_factory=list)


class StorageUnitView(BaseModel):
    """A cabinet / shelf / drawer-cabinet / box."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    room_id: uuid.UUID
    name: str
    unit_type: str
    sort_order: int = 0
    sections: list[StorageSectionView] = Field(default_factory=list)


class RoomTreeView(RoomView):
    """A room with its units (and their sections/slots) nested."""

    model_config = ConfigDict(extra="forbid")

    units: list[StorageUnitView] = Field(default_factory=list)


class SpaceTreeView(BaseModel):
    """The whole home → rooms → units → sections → slots hierarchy."""

    model_config = ConfigDict(extra="forbid")

    home: HomeView
    rooms: list[RoomTreeView] = Field(default_factory=list)


class MemberView(BaseModel):
    """One row in a home's member list (P0.8).

    ``display_name`` and ``email`` come from the ``User`` row joined to the
    ``HomeMembership``; ``role`` and ``joined_at`` from the membership. The
    endpoint that emits this (``GET /homes/{id}/members``) is open to any
    member of the home so people can see who else shares the home, not just
    owners.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    display_name: str
    email: EmailStr
    role: HomeRole
    joined_at: datetime


class MemberInviteRequest(BaseModel):
    """Body of ``POST /homes/{id}/members``.

    Email must match an existing ``User``; unknown emails come back as
    ``404 not_found`` so the caller can tell the friend to register first.
    There is no email/SMTP path in this build, so the call IS the invite.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: HomeRole = HomeRole.MEMBER


class MemberUpdateRequest(BaseModel):
    """Body of ``PATCH /homes/{id}/members/{user_id}``.

    Only the role changes — there is nothing else on a membership worth
    editing. ``extra="forbid"`` keeps a typo'd field from silently no-op'ing.
    """

    model_config = ConfigDict(extra="forbid")

    role: HomeRole


# ------------------------------------------------------------------- factories
#
# The read routes in ``app/api/v1/homes.py`` and the write routes in
# ``app/api/v1/structure.py`` both project rows into these views, so the
# projection lives here rather than privately in one of them. Input is the
# JSON-shaped ``dict`` the tool layer already produces; the write routes build
# one from the ORM row they just created.


def room_view(data: dict[str, Any], *, unit_count: int = 0) -> RoomView:
    return RoomView(
        id=data["id"],
        home_id=data["home_id"],
        name=data["name"],
        room_type=data["room_type"],
        sort_order=int(data.get("sort_order") or 0),
        unit_count=unit_count,
    )


def slot_view(data: dict[str, Any]) -> StorageSlotView:
    return StorageSlotView(
        id=data["id"],
        section_id=data["section_id"],
        code=data["code"],
        label=data.get("label"),
        capacity_hint=data.get("capacity_hint"),
        allowed_categories=list(data.get("allowed_categories") or []),
        sort_order=int(data.get("sort_order") or 0),
        active_count=int(data.get("active_count") or 0),
    )


def section_view(
    data: dict[str, Any], *, slots: list[StorageSlotView]
) -> StorageSectionView:
    return StorageSectionView(
        id=data["id"],
        unit_id=data["unit_id"],
        name=data["name"],
        section_type=data["section_type"],
        sort_order=int(data.get("sort_order") or 0),
        slots=slots,
    )


def unit_view(
    data: dict[str, Any], *, sections: list[StorageSectionView]
) -> StorageUnitView:
    return StorageUnitView(
        id=data["id"],
        room_id=data["room_id"],
        name=data["name"],
        unit_type=data["unit_type"],
        sort_order=int(data.get("sort_order") or 0),
        sections=sections,
    )


__all__ = [
    "HomeView",
    "MemberInviteRequest",
    "MemberUpdateRequest",
    "MemberView",
    "RoomTreeView",
    "RoomView",
    "SpaceTreeView",
    "StorageSectionView",
    "StorageSlotView",
    "StorageUnitView",
    "room_view",
    "section_view",
    "slot_view",
    "unit_view",
]
