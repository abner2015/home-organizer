"""Recommendation agent — the 9-step pipeline that maps an Item to a StorageSlot.

Public entry points:

- :func:`RecommendationAgent.run` — full pipeline, returns a
  :class:`AgentRunResult`.
- :class:`RecommendationState` — the state-machine enum.
- :class:`AgentRunResult` / :class:`AgentStepResult` — orchestrator outputs.
"""
from app.agents.pipeline import AgentRunResult, RecommendationAgent
from app.agents.state import AgentStepResult, RecommendationState

__all__ = [
    "AgentRunResult",
    "AgentStepResult",
    "RecommendationAgent",
    "RecommendationState",
]
