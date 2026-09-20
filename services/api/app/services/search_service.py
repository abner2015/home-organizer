"""Search service — wraps :class:`SearchAgent` and persists the AgentTrace.

The search agent itself is a pure orchestrator (read-only against the
DB). This service:

1. Builds a fresh ``ToolRegistry`` bound to the request's session.
2. Runs the agent and gets back a :class:`SearchRunResult`.
3. Persists an ``AgentTrace`` row for observability (no ``Recommendation``
   row, since search is read-only).
4. Surfaces a view-shaped dict the API can wrap in a Pydantic schema.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search import SearchAgent, SearchRunResult
from app.ai.provider import AIProvider
from app.models import AgentTrace
from app.tools.registry import get_default_registry

# --------------------------------------------------------------------------- value objects


@dataclass(slots=True)
class SearchOutcome:
    """Public-shaped search outcome for one run."""

    result: SearchRunResult
    trace_id: uuid.UUID | None


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
) -> SearchOutcome:
    """Run the search agent + persist an ``AgentTrace`` row.

    The trace row is the only DB write the search endpoint makes — search
    itself is read-only against the home's items / slots / placements.
    """
    tools = get_default_registry(db)
    agent = SearchAgent(ai=provider, tools=tools, db=db)
    result = await agent.run(home_id=home_id, user_id=user_id, query=query)

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

    return SearchOutcome(result=result, trace_id=trace.id)


__all__ = ["SearchOutcome", "run_search"]
