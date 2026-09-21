"""Pydantic schemas for asset upload / fetch responses."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class AssetUploadResponse(BaseModel):
    """Response body for POST /api/v1/assets/upload."""

    asset_id: UUID
    object_key: str = Field(description="Storage key; used to attach to an item")
    url: str = Field(description="Signed GET URL (default TTL 1h)")
    content_type: str
    size: int = Field(description="Uploaded body size in bytes")
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    deduplicated: bool = Field(
        default=False,
        description="True if an existing asset with the same sha256 was reused.",
    )


class AssetResponse(BaseModel):
    """Response body for GET /api/v1/assets/{id}."""

    asset_id: UUID
    home_id: UUID
    created_by: UUID
    content_type: str
    size: int
    width: int | None = None
    height: int | None = None
    sha256: str | None = None
    original_filename: str | None = None
    status: str
    failure_reason: str | None = None
    uploaded_at: datetime | None = None
    created_at: datetime
    url: str = Field(description="Freshly issued presigned GET URL")
