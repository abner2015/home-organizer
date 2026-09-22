"""Pydantic schemas for the item read API (Phase 10).

The shapes mirror ``apps/web/src/lib/types.ts`` — in particular
``Item.current_placement`` (which the dashboard uses to count un-placed items
and the item card uses to print a location) and ``ItemPlacement.slot_path``.
Neither is directly derivable from a row, so the router joins the storage
hierarchy to fill them.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.recommendation import CandidateView

EstimatedSize = Literal["small", "medium", "large"]


class PlacementRefView(BaseModel):
    """Where an item currently lives — a slot id plus its display path."""

    model_config = ConfigDict(extra="forbid")

    slot_id: uuid.UUID
    slot_path: str = ""


class ItemView(BaseModel):
    """One item, enriched for the UI."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    home_id: uuid.UUID
    name: str
    description: str | None = None
    category: str | None = None
    subcategory: str | None = None
    estimated_size: str | None = None
    is_sensitive: bool = False
    needs_lock: bool = False
    primary_image_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    current_placement: PlacementRefView | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ItemCreateRequest(BaseModel):
    """Body for ``POST /items``.

    ``image_object_keys`` are MinIO keys issued by ``POST /uploads/presign``.
    The router checks each one is under the caller's home prefix before
    linking it, so a client can't attach another home's object.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    subcategory: str | None = Field(default=None, max_length=100)
    estimated_size: EstimatedSize | None = None
    is_sensitive: bool = False
    needs_lock: bool = False
    image_object_keys: list[str] = Field(default_factory=list, max_length=10)
    primary_image_object_key: str | None = Field(default=None, max_length=512)


class ItemUpdateRequest(BaseModel):
    """Body for ``PATCH /items/{id}``. Omitted fields are left untouched."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    subcategory: str | None = Field(default=None, max_length=100)
    estimated_size: EstimatedSize | None = None
    is_sensitive: bool | None = None
    needs_lock: bool | None = None


class ItemVisionView(BaseModel):
    """The recognised attributes of one item — the ``vision`` object in
    ``POST /items/{id}/vision``.

    Mirrors the shape documented in ``docs/API.md`` §7 and consumed by the
    Web app. It is an *adapter* over :class:`app.ai.provider.VisionOutput`,
    which names its fields differently (``notes``/``size_class``); the router
    does the translation so the AI module's contract can evolve independently
    of the public API.

    ``confidence`` and ``attributes`` are ``None``/empty because the Phase 4
    Vision schema does not produce them — see the router for details.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    category: str
    subcategory: str = ""
    description: str = ""
    confidence: float | None = None
    is_sensitive: bool = False
    needs_lock: bool = False
    attributes: list[str] = Field(default_factory=list)
    estimated_size: EstimatedSize | None = None


class ItemVisionResponse(BaseModel):
    """Response body for ``POST /items/{id}/vision``."""

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    vision: ItemVisionView
    trace_id: uuid.UUID | None = None


class InferItemRequest(BaseModel):
    """Body for ``POST /items/infer`` — a name (and optional note) to fill in.

    ``extra="forbid"`` for hygiene but no ``strict``: JSON client input.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class ItemInferenceResponse(BaseModel):
    """Response body for ``POST /items/infer``.

    ``vision`` is the same shape ``POST /items/{id}/vision`` returns, so the
    Web app can prefill the confirm form from either endpoint with one code
    path. Nothing is persisted — the CTA flow creates the item afterwards.
    """

    model_config = ConfigDict(extra="forbid")

    vision: ItemVisionView
    trace_id: uuid.UUID | None = None


class PaginatedItemsView(BaseModel):
    """``GET /items`` page envelope."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemView] = Field(default_factory=list)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class ItemPlacementView(BaseModel):
    """One row of an item's placement history, with its slot path.

    ``reason`` is the "为什么放这里" text (P0.4). It is populated for a placement
    that came from an AI recommendation — the reason recorded on that
    recommendation's candidate. A manual placement has nothing to explain and
    leaves it empty, which is why the field defaults to ``""`` rather than
    being required.
    """

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    item_id: uuid.UUID
    slot_id: uuid.UUID
    slot_path: str | None = None
    source: str
    placed_at: str
    removed_at: str | None = None
    note: str | None = None
    reason: str = Field(default="", max_length=512)


class PlaceItemRequest(BaseModel):
    """Body for ``POST /placements`` — put an item straight into a slot.

    ``extra="forbid"`` for hygiene but no ``strict``: JSON client input, so
    UUIDs arrive as strings. Both ids are re-checked against the caller's home
    by the service (cross-home → 404).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    slot_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


class CandidateListResponse(BaseModel):
    """``GET /items/{id}/candidates`` — the deterministic pre-LLM view.

    No DECIDE step runs, so there is no *chosen* candidate — callers get the
    ranked list plus the filter counts. Each candidate still carries a
    ``reason``: since it is built by the ranker rather than the model, it is
    available without an LLM call at all.
    """

    model_config = ConfigDict(extra="forbid")

    pre_filter_count: int = Field(ge=0)
    post_filter_count: int = Field(ge=0)
    final_candidates: list[CandidateView] = Field(default_factory=list)


__all__ = [
    "CandidateListResponse",
    "ItemCreateRequest",
    "ItemPlacementView",
    "ItemUpdateRequest",
    "ItemView",
    "ItemVisionResponse",
    "ItemVisionView",
    "PaginatedItemsView",
    "PlaceItemRequest",
    "PlacementRefView",
]
