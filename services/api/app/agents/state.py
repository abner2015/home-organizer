"""State machine + step-result dataclass for the recommendation agent.

The pipeline follows the 9 steps defined in ``docs/AGENT.md``:

    INTAKE → UNDERSTAND → RETRIEVE → CANDIDATE_GENERATION → FILTER
      → RANK → DECIDE → VERIFY → (RETRY → DECIDE …) → ANSWER
      └──────────────────────────────────────────────→ FAILED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RecommendationState(StrEnum):
    """States of the recommendation pipeline.

    Values are stored as lowercase strings in the AgentTrace row's ``steps``
    JSONB blob so log queries can filter on them.
    """

    INTAKE = "intake"
    UNDERSTAND = "understand"
    RETRIEVE = "retrieve"
    CANDIDATE_GENERATION = "candidate_generation"
    FILTER = "filter"
    RANK = "rank"
    DECIDE = "decide"
    VERIFY = "verify"
    RETRY = "retry"
    ANSWER = "answer"
    FAILED = "failed"


@dataclass(slots=True)
class AgentStepResult:
    """A single step's audit record (one row in AgentTrace.steps)."""

    state: RecommendationState
    started_at: datetime
    ended_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "payload": self.payload,
            "error": self.error,
        }


__all__ = ["AgentStepResult", "RecommendationState"]
