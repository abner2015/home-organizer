"""Pydantic schemas for the natural-language search API (Phase 6).

The API exposes one endpoint: ``POST /api/v1/search``.

Request body:
    {"query": "我的数据线在哪里？"}

Response body mirrors the agent's :class:`SearchRunResult`:

- ``answer_text`` — pre-formatted Chinese answer (always present, even on
  errors / not-found, so the UI can render a single string).
- ``state`` — one of ``answer``, ``needs_clarification``, ``not_found``,
  ``exists_but_not_placed``, ``error``.
- ``intent`` — the parsed SearchIntentKind value (``find_item`` etc.).
- ``matches`` — structured list of candidate items with their slot
  references (the UI can render these as a card list).
- ``clarification_question`` — present when ``state=needs_clarification``.
- ``trace_id`` — AgentTrace row id for observability / debugging.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- request


class SearchRequest(BaseModel):
    """Request body for ``POST /api/v1/search``.

    ``extra="forbid"`` rejects extra fields (security). ``strict=True`` is
    NOT used — JSON deserialization produces strings for UUIDs and Pydantic
    strict mode would reject them. (Same convention as RecognizeRequest.)
    """

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        min_length=1, max_length=512, description="User's natural-language question."
    )


# --------------------------------------------------------------------------- response


class SlotRef(BaseModel):
    """Reference to a StorageSlot inside a candidate match."""

    model_config = ConfigDict(extra="forbid")

    slot_id: uuid.UUID
    code: str = ""
    label: str = ""
    room_name: str = ""
    unit_name: str = ""
    section_name: str = ""
    full_path: str = ""


class CandidateMatch(BaseModel):
    """One item-level match inside the response.

    ``location`` is null when the item exists in the database but has no
    active placement (the API surfaces this so the UI can render "found but
    not placed yet" instead of pretending we know where it is).
    """

    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    name: str
    category: str = ""
    subcategory: str = ""
    brand: str | None = None
    is_sensitive: bool = False
    location: SlotRef | None = None


class SearchResponse(BaseModel):
    """Response body for ``POST /api/v1/search``.

    The UI should always show ``answer_text``; the structured ``matches`` and
    ``state`` are for richer rendering (e.g. a follow-up card list when
    ``state=needs_clarification``).
    """

    model_config = ConfigDict(extra="forbid")

    answer_text: str
    state: str = Field(
        description=(
            "One of: 'answer' (single match with location), "
            "'needs_clarification' (multiple matches — pick one), "
            "'not_found' (zero matches), "
            "'exists_but_not_placed' (item found in DB but no active placement), "
            "'error' (provider failure)."
        ),
    )
    intent: str = Field(description="The parsed SearchIntentKind value.")
    matches: list[CandidateMatch] = Field(
        default_factory=list,
        max_length=10,
        description="Top candidate matches (up to 10) with slot references.",
    )
    clarification_question: str | None = Field(
        default=None,
        description="When state='needs_clarification', the question to send back to the user.",
    )
    trace_id: uuid.UUID | None = Field(
        default=None, description="AgentTrace row id for debugging."
    )


__all__ = [
    "CandidateMatch",
    "SearchRequest",
    "SearchResponse",
    "SlotRef",
]
