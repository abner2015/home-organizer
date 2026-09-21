"""Natural-language search endpoint (Phase 6).

One endpoint: ``POST /api/v1/search``.

The body is ``{"query": "我的数据线在哪里？"}`` plus an optional
``conversation_id``. The agent runs, records the turn, persists an
``AgentTrace`` for observability, and returns the final ``SearchResponse``
with a Chinese ``answer_text`` the UI can display verbatim.

The endpoint always returns ``200`` on success. When the LLM is
unreachable, the agent's ``state`` becomes ``"error"`` and the body
still includes a well-formed ``answer_text`` so the UI never has to
branch on transport-level errors.

Stub auth via ``X-User-Id`` / ``X-Home-Id`` headers — same as every
other endpoint in this milestone.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.factory import get_provider
from app.ai.provider import AIProvider
from app.api.deps import Actor, get_actor
from app.db.session import get_db
from app.schemas.search import (
    CandidateMatch,
    SearchRequest,
    SearchResponse,
    SlotRef,
)
from app.services import search_service

router = APIRouter(prefix="/search", tags=["search"])


# ---------------------------------------------------------------------- helpers


def _get_ai_provider() -> AIProvider:
    """FastAPI dependency. Overridden in tests to inject a mock provider."""
    return get_provider()


def _slot_ref_model(raw: dict[str, object]) -> SlotRef:
    """Project a slot dict into the public ``SlotRef`` shape."""
    return SlotRef(
        slot_id=raw.get("slot_id"),
        code=str(raw.get("code") or ""),
        label=str(raw.get("label") or ""),
        room_name=str(raw.get("room_name") or ""),
        unit_name=str(raw.get("unit_name") or ""),
        section_name=str(raw.get("section_name") or ""),
        full_path=str(raw.get("full_path") or ""),
    )


def _candidate_to_match(c: dict[str, object]) -> CandidateMatch:
    """Project an internal candidate dict into the public response shape."""
    raw_loc = c.get("location")
    location = _slot_ref_model(raw_loc) if isinstance(raw_loc, dict) else None
    return CandidateMatch(
        item_id=c.get("item_id"),
        name=str(c.get("name") or ""),
        category=str(c.get("category") or ""),
        subcategory=str(c.get("subcategory") or ""),
        is_sensitive=bool(c.get("is_sensitive", False)),
        location=location,
    )


# ---------------------------------------------------------------------- endpoint


@router.post(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Natural-language search over a home's items",
)
async def search(
    payload: SearchRequest,
    actor: Annotated[Actor, Depends(get_actor)],
    db: Annotated[AsyncSession, Depends(get_db)],
    provider: Annotated[AIProvider, Depends(_get_ai_provider)],
) -> SearchResponse:
    """Resolve a natural-language Chinese query and return a final answer.

    The turn is recorded in a conversation, so the assistant remembers what
    was said. Pass the returned ``conversation_id`` back on the next request
    to continue it; omit it to start a new one.

    Failures:

    - ``422`` — request body is malformed (extra fields, missing query,
      too long). Handled by FastAPI's request validation.
    - ``404`` — ``conversation_id`` names a conversation that doesn't exist
      (or belongs to another home / user).
    - All other failures collapse to a ``state='error'`` payload with a
      well-formed ``answer_text``; the HTTP status is still ``200``.
    """
    outcome = await search_service.run_search(
        db,
        provider=provider,
        home_id=actor.home_id,
        user_id=actor.user_id,
        query=payload.query,
        conversation_id=payload.conversation_id,
    )
    await db.commit()
    result = outcome.result
    matches = [_candidate_to_match(m) for m in result.matches]
    # When the agent surfaces a clarification request, the follow-up
    # question lives on either ``intent.question`` (UNKNOWN / LLM-asked
    # clarification) or ``answer_text`` (multiple-match disambiguation).
    # Pick whichever is non-empty.
    clarification_question: str | None = None
    if result.state == "needs_clarification":
        clarification_question = (
            result.intent.question.strip() or outcome.answer_text
        )
    suggested_slot = (
        _slot_ref_model(result.suggested_slot)
        if isinstance(result.suggested_slot, dict)
        else None
    )
    return SearchResponse(
        answer_text=outcome.answer_text,
        state=result.state,
        intent=result.intent.intent.value,
        matches=matches,
        clarification_question=clarification_question,
        suggested_slot=suggested_slot,
        suggested_reason=result.suggested_reason,
        suggested_item_name=result.suggested_item_name,
        conversation_id=outcome.conversation_id,
        trace_id=outcome.trace_id,
    )


__all__ = ["router"]
