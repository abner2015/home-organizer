"""Search service — wraps :class:`SearchAgent`, adds memory, persists traces.

The search agent itself is a pure orchestrator (read-only against the DB and
memoryless). This service owns everything stateful:

1. Resolves the caller's conversation and loads its transcript
   (:mod:`app.services.conversation_service`) so a follow-up query has
   context.
2. Builds a fresh ``ToolRegistry`` bound to the request's session and runs
   the agent.
3. Asks the chat model to phrase the final reply from the facts the agent
   retrieved (``compose_answer``), falling back to the deterministic draft.
4. Persists the two messages of the turn plus an ``AgentTrace`` row for
   observability (no ``Recommendation`` row, since search is read-only).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search import SearchAgent, SearchRunResult
from app.agents.search.answer import compose_answer
from app.ai.provider import AIProvider
from app.models import AgentTrace
from app.services import conversation_service
from app.tools.registry import get_default_registry

# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class SearchOutcome:
    """Public-shaped search outcome for one run.

    ``answer_text`` is what the user should see: the LLM's phrasing of
    ``result.answer_text`` (the deterministic draft), or the draft itself when
    the chat call failed or there was nothing to phrase.
    """

    result: SearchRunResult
    trace_id: uuid.UUID | None
    conversation_id: uuid.UUID
    answer_text: str


# --------------------------------------------------------------------------- trace helpers


def _final_status(result: SearchRunResult) -> str:
    """Project the agent's state onto the ``AgentTrace.final_status`` column.

    Allowed column values are ``"success"`` / ``"verifier_failed"`` /
    ``"error"`` (per the model's CHECK constraint). The search agent
    doesn't run a verifier, so we collapse to ``"success"`` or ``"error"``.
    """
    if result.state == "error":
        return "error"
    return "success"


def _steps_json(result: SearchRunResult) -> list[dict[str, Any]]:
    """Trace shape consumed by /observability.

    The search agent is a single intent-extract LLM call + tool dispatch,
    so we record one step carrying the intent + state + match count. This
    matches the recommendation agent's convention of ``steps`` as a JSONB
    array of step dicts.
    """
    return [
        {
            "state": "intent_extraction",
            "status": result.state,
            "intent": result.intent.intent.value,
            "match_count": len(result.matches),
        }
    ]


# --------------------------------------------------------------------------- service


async def run_search(
    db: AsyncSession,
    *,
    provider: AIProvider,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    query: str,
    conversation_id: uuid.UUID | None = None,
) -> SearchOutcome:
    """Run one conversational turn: memory → agent → phrasing → persistence.

    ``conversation_id=None`` starts a new conversation; the id to send on the
    next turn comes back on :class:`SearchOutcome`. An unknown / foreign
    conversation id raises :class:`NotFoundError` (propagated from
    :func:`conversation_service.begin_turn`).

    Search itself is still read-only against the home's items / slots /
    placements; the writes are the two ``Message`` rows and the
    ``AgentTrace``.
    """
    context = await conversation_service.begin_turn(
        db,
        conversation_id=conversation_id,
        home_id=home_id,
        user_id=user_id,
    )

    tools = get_default_registry(db)
    agent = SearchAgent(ai=provider, tools=tools, db=db)
    result = await agent.run(
        home_id=home_id,
        user_id=user_id,
        query=query,
        history=context.history,
    )

    # Phrase the deterministic draft in the model's own words. Skipped on
    # ``state='error'`` — there is nothing but a system message to reword, and
    # we do not want to fire a second (possibly failing) call while the
    # provider is already down.
    answer_text = result.answer_text
    if result.state != "error":
        answer_text = await compose_answer(
            provider,
            user_query=query,
            draft=result.answer_text,
            history=context.history,
        )

    await conversation_service.record_turn(
        db,
        conversation_id=context.conversation_id,
        user_query=query,
        answer_text=answer_text,
    )

    trace = AgentTrace(
        home_id=home_id,
        user_id=user_id,
        item_id=None,
        steps=_steps_json(result),
        final_status=_final_status(result),
        total_duration_ms=0,  # the intent call is fast; skip timing for now
        error=None,
    )
    db.add(trace)
    await db.flush()

    return SearchOutcome(
        result=result,
        trace_id=trace.id,
        conversation_id=context.conversation_id,
        answer_text=answer_text,
    )


__all__ = ["SearchOutcome", "run_search"]
