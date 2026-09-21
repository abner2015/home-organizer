"""Pydantic schemas for the home / storage-structure read API.

These mirror the shapes the Web app's ``src/lib/types.ts`` declares, so the
``/home``, ``/home/rooms`` and ``/home/storage`` pages can render the seeded
hierarchy directly. Like every response model in this package they are
``extra="forbid"`` (hygiene) without ``strict`` — the payloads are built from
JSON-safe dicts whose UUIDs are strings.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


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


__all__ = [
    "HomeView",
    "RoomTreeView",
    "RoomView",
    "SpaceTreeView",
    "StorageSectionView",
    "StorageSlotView",
    "StorageUnitView",
]
