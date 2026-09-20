"""Search agent (Phase 6).

Public surface:

- :data:`SearchIntentKind` — the six user-intent enum entries.
- :data:`ExtractedSearchIntent` — the LLM-structured intent payload.
- :func:`extract_intent` — single LLM call that parses a user query.
- :class:`SearchAgent` — orchestrator that turns an intent + DB tools
  into a final :class:`SearchRunResult`.
- :data:`SearchRunResult` — orchestrator output (state + answer + matches).
"""

from app.agents.search.agent import SearchAgent, SearchRunResult
from app.agents.search.intent import (
    ExtractedSearchIntent,
    SearchIntentKind,
    extract_intent,
)

__all__ = [
    "ExtractedSearchIntent",
    "SearchAgent",
    "SearchIntentKind",
    "SearchRunResult",
    "extract_intent",
]
