"""Pydantic schemas for the recommendation API (Phase 5).

Four endpoints:

- POST /api/v1/recommendations/items/{item_id}/recommend  → RecommendResponse
- POST /api/v1/recommendations/{rec_id}/accept             → AcceptResponse
- POST /api/v1/recommendations/{rec_id}/reject             → RejectResponse
- PATCH /api/v1/recommendations/{rec_id}                   → PatchResponse
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- recommend


class RecommendRequest(BaseModel):
    """Request body for ``POST /recommend``. Currently empty — the only input
    is the ``item_id`` path param + actor headers. Provided as a separate
    schema so future knobs (e.g. ``include_reasons: bool``) have a home."""

    model_config = ConfigDict(extra="forbid")


class CandidateView(BaseModel):
    """One candidate slot in the RecommendResponse — what the UI uses to draw
    a top-3 list and a primary "recommended" highlight."""

    model_config = ConfigDict(extra="forbid")

    slot_id: uuid.UUID
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(default="", max_length=128)
    full_path: str = Field(default="", max_length=256)
    room_name: str = Field(default="", max_length=64)
    unit_name: str = Field(default="", max_length=64)
    score: int = Field(ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=512)


class RecommendResponse(BaseModel):
    """Response body for ``POST /recommend``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    item_id: uuid.UUID
    chosen_slot_id: uuid.UUID | None = Field(
        default=None,
        description="None when the pipeline ended in FAILED (no safe candidate).",
    )
    status: str = Field(
        description="Recommendation status — 'pending' on success, 'pending' "
        "with chosen_slot_id=null on FAILED.",
    )
    state: str = Field(description="Final pipeline state: 'answer' or 'failed'.")
    retries_used: int = Field(ge=0, le=10)
    pre_filter_count: int = Field(ge=0)
    post_filter_count: int = Field(ge=0)
    candidates: list[CandidateView] = Field(
        min_length=0, max_length=3,
        description="Top-3 candidates surfaced to the user; empty when failed.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable failure reason (only set when state='failed').",
    )


# --------------------------------------------------------------------------- accept


class AcceptRequest(BaseModel):
    """Request body for ``POST /recommendations/{rec_id}/accept``."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=512)


class PlacementView(BaseModel):
    """View of an ItemPlacement row returned by accept."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    item_id: uuid.UUID
    slot_id: uuid.UUID
    source: str
    is_active: bool
    note: str | None = None


class AcceptResponse(BaseModel):
    """Response body for ``POST /recommendations/{rec_id}/accept``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    status: str = Field(description="Always 'accepted' on success.")
    placement: PlacementView


# --------------------------------------------------------------------------- reject


class RejectRequest(BaseModel):
    """Request body for ``POST /recommendations/{rec_id}/reject``."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=512)


class RejectResponse(BaseModel):
    """Response body for ``POST /recommendations/{rec_id}/reject``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    status: str = Field(description="Always 'rejected' on success.")
    note: str | None = None


# --------------------------------------------------------------------------- patch


class PatchRequest(BaseModel):
    """Request body for ``PATCH /recommendations/{rec_id}``.

    The user can move the ``chosen_slot_id`` to any slot in the same home.
    The status stays ``pending`` until the user accepts.
    """

    model_config = ConfigDict(extra="forbid")

    chosen_slot_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=512)


class PatchResponse(BaseModel):
    """Response body for ``PATCH /recommendations/{rec_id}``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    status: str = Field(description="Always 'pending' after a patch.")
    chosen_slot_id: uuid.UUID
    candidates: list[CandidateView] = Field(
        description="Updated candidates list with the new chosen_slot surfaced.",
    )


__all__ = [
    "AcceptRequest",
    "AcceptResponse",
    "CandidateView",
    "PatchRequest",
    "PatchResponse",
    "PlacementView",
    "RecommendRequest",
    "RecommendResponse",
    "RejectRequest",
    "RejectResponse",
]


# Silence unused-import warning while keeping the Any symbol handy for future
# nested schema fields (e.g. per-candidate matched_rules list).
_ = (Any,)
