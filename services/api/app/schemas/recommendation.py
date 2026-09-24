"""Pydantic schemas for the recommendation API (Phase 5).

Six endpoints:

- POST /api/v1/recommendations/items/{item_id}/recommend  → RecommendResponse
- POST /api/v1/recommendations/{rec_id}/accept             → AcceptResponse
- POST /api/v1/recommendations/{rec_id}/reject             → RejectResponse
- POST /api/v1/recommendations/{rec_id}/revoke             → RevokeResponse
- POST /api/v1/recommendations/bulk-revoke                → BulkRevokeResponse
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
    a top-3 list and a primary "recommended" highlight.

    ``code`` is not required: a slot the user PATCHed onto a recommendation
    that the LLM never surfaced has no code until it is joined back to the
    live slot row.
    """

    model_config = ConfigDict(extra="forbid")

    slot_id: uuid.UUID
    code: str = Field(default="", max_length=64)
    label: str = Field(default="", max_length=128)
    full_path: str = Field(default="", max_length=256)
    room_name: str = Field(default="", max_length=64)
    unit_name: str = Field(default="", max_length=64)
    section_name: str = Field(default="", max_length=64)
    score: int = Field(ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=512)
    matched_rules: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    is_recommended: bool = Field(
        default=False,
        description="True for the slot the agent (or the user's PATCH) chose.",
    )


class RecommendResponse(BaseModel):
    """Response body for ``POST /recommend`` and ``GET /recommendations/{id}``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    item_id: uuid.UUID
    trace_id: uuid.UUID = Field(
        description="AgentTrace id of the run that produced this recommendation."
    )
    chosen_slot_id: uuid.UUID | None = Field(
        default=None,
        description="None when the pipeline ended in FAILED (no safe candidate).",
    )
    status: str = Field(
        description="Recommendation lifecycle status: 'pending', 'accepted', "
        "'rejected', 'revoked' or 'superseded'.",
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


# --------------------------------------------------------------------------- bulk-revoke (P0.B)


class BulkRevokeRequest(BaseModel):
    """Request body for ``POST /recommendations/bulk-revoke``.

    Bulk-revoke un-does a batch of ``rejected`` recommendations in one round-trip
    and, when ``auto_rerun=True``, immediately re-runs the recommendation pipeline
    for every affected item so the user sees fresh candidates without a second
    click.

    Hard cap at 50 IDs keeps the request cheap enough for an online UI round-trip
    — if a user somehow accumulated more rejected recs than that, the front-end
    can page its own list and call this endpoint twice.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)
    auto_rerun: bool = False


class BulkRevokeRevokedItem(BaseModel):
    """One successfully-revoked recommendation. ``status`` is always ``'revoked'``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    status: str = Field(description="Always 'revoked' on success.")


class BulkRevokeRerunItem(BaseModel):
    """Per-item outcome of an auto-rerun triggered by bulk-revoke.

    Mirrors the top-level fields of ``RecommendResponse`` so the UI can render the
    new candidates without a second GET. ``new_recommendation_id`` is ``None`` and
    ``state='failed'`` when the rerun pipeline returned no safe candidate.
    """

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    new_recommendation_id: uuid.UUID | None = Field(
        default=None,
        description="None when the rerun pipeline ended in FAILED.",
    )
    state: str = Field(description="'success' or 'failed'.")
    chosen_slot_id: uuid.UUID | None = Field(
        default=None,
        description="None when state='failed'.",
    )
    candidates: list[CandidateView] = Field(
        min_length=0, max_length=3,
        description="Top-3 candidates from the rerun; empty when failed.",
    )


class BulkRevokeError(BaseModel):
    """One failed entry inside a bulk-revoke response.

    Exactly one of ``recommendation_id`` (revoke-side failure) or ``item_id``
    (rerun-side failure) is set, never both — this keeps the error stream
    unambiguous to the UI's per-row rendering.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID | None = Field(
        default=None,
        description="Set when the failure happened during the revoke phase.",
    )
    item_id: uuid.UUID | None = Field(
        default=None,
        description="Set when the failure happened during the auto-rerun phase.",
    )
    code: str = Field(
        description="'not_found' | 'conflict' | 'ai_error' — stable for client branching.",
    )
    message: str


class BulkRevokeResponse(BaseModel):
    """Response body for ``POST /recommendations/bulk-revoke``.

    The endpoint always returns 200; per-entry failures are reported inside
    ``errors[]``. This keeps the front-end from needing to handle a half-success
    top-level 4xx — it always reads three arrays and renders each.
    """

    model_config = ConfigDict(extra="forbid")

    revoked: list[BulkRevokeRevokedItem] = Field(
        description="Successfully-revoked recommendations.",
    )
    rerun_results: list[BulkRevokeRerunItem] = Field(
        description="One entry per affected item when auto_rerun=True; empty otherwise.",
    )
    errors: list[BulkRevokeError] = Field(
        description="Per-entry failures; empty when everything succeeded.",
    )


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


# --------------------------------------------------------------------------- revoke


class RevokeRequest(BaseModel):
    """Request body for ``POST /recommendations/{rec_id}/revoke``. Empty.

    Revoke is an un-do of reject — there's no new fact to record, so the body
    has no fields. ``extra='forbid'`` keeps the contract strict.
    """

    model_config = ConfigDict(extra="forbid")


class RevokeResponse(BaseModel):
    """Response body for ``POST /recommendations/{rec_id}/revoke``."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: uuid.UUID
    status: str = Field(description="Always 'revoked' on success.")


__all__ = [
    "AcceptRequest",
    "AcceptResponse",
    "BulkRevokeError",
    "BulkRevokeRequest",
    "BulkRevokeRerunItem",
    "BulkRevokeResponse",
    "BulkRevokeRevokedItem",
    "CandidateView",
    "PatchRequest",
    "PatchResponse",
    "PlacementView",
    "RecommendRequest",
    "RecommendResponse",
    "RejectRequest",
    "RejectResponse",
    "RevokeRequest",
    "RevokeResponse",
]


# Silence unused-import warning while keeping the Any symbol handy for future
# nested schema fields (e.g. per-candidate matched_rules list).
_ = (Any,)
