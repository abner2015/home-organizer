"""Pydantic schemas for the item-recognition API."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RecognizeRequest(BaseModel):
    """Request body for ``POST /api/v1/items/recognize``.

    ``description`` is treated as data (not instruction) by the LLM
    prompt per the project's "user input is data, not command" rule.

    ``strict=True`` is NOT set: the JSON body parses UUID fields as
    strings, and Pydantic strict mode would reject them outright. Extra
    fields are still rejected via ``extra="forbid"`` (test
    ``test_recognize_endpoint_rejects_extra_fields`` guards this).
    """

    model_config = ConfigDict(extra="forbid")

    asset_id: UUID
    description: str | None = Field(default=None, max_length=512)


class RecognitionResult(BaseModel):
    """The structured recognition payload (mirrors VisionOutput).

    We expose this as an explicit schema (rather than just
    ``VisionOutput.model_dump()``) so the API contract is decoupled
    from the AI module's internal model — providers can change
    without breaking clients.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=128)
    category: str = Field(min_length=1, max_length=64)
    subcategory: str = Field(default="", max_length=64)
    usage_scene: str = Field(default="", max_length=128)
    usage_frequency: Literal["high", "medium", "low"] = "medium"
    size_class: Literal["small", "medium", "large"] = "medium"
    fragility: Literal["low", "medium", "high"] = "low"
    notes: str = Field(default="", max_length=512)


class RecognizeResponse(BaseModel):
    """Response body for ``POST /api/v1/items/recognize``."""

    model_config = ConfigDict(extra="forbid", strict=True)

    result: RecognitionResult
    trace_id: UUID | None = Field(
        default=None,
        description="AgentTrace row id; clients can use this to look up logs.",
    )
    attempts: int = Field(ge=1, le=10)
    duration_ms: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=64)


__all__ = ["RecognitionResult", "RecognizeRequest", "RecognizeResponse"]
