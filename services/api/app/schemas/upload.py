"""Pydantic schemas for the direct-to-storage upload flow.

``POST /uploads/presign`` issues a presigned PUT URL so the browser can send
the bytes straight to MinIO without the API proxying them (``docs/API.md`` §6,
mirrored by ``apps/web/src/lib/types.ts``).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PresignUploadRequest(BaseModel):
    """Body for ``POST /uploads/presign``."""

    model_config = ConfigDict(extra="forbid")

    file_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)


class PresignUploadResponse(BaseModel):
    """Body of a successful ``POST /uploads/presign``.

    ``object_key`` is generated server-side — the client's ``file_name`` is
    never used as a key (path traversal / prefix squatting).
    """

    model_config = ConfigDict(extra="forbid")

    upload_url: str
    object_key: str
    method: Literal["PUT"] = "PUT"
    expires_in: int = Field(ge=1)


__all__ = ["PresignUploadRequest", "PresignUploadResponse"]
