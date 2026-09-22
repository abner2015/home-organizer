"""AgentContext — the state carrier that flows through the pipeline."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.tools.registry import ToolRegistry
from app.verification.context import VerificationContext


@dataclass(slots=True)
class AgentContext:
    """All inputs + intermediate state for one recommendation run.

    Fields are populated as the pipeline progresses; the orchestrator
    (:class:`RecommendationAgent`) constructs an empty context and fills it
    step by step, persisting each :class:`AgentStepResult` along the way.
    """

    # ---- Inputs ----
    home_id: uuid.UUID
    user_id: uuid.UUID
    item_id: uuid.UUID
    tools: ToolRegistry
    max_retries: int = 2

    # ---- Populated as we go ----
    item: dict[str, Any] | None = None
    home: dict[str, Any] | None = None
    rooms: list[dict[str, Any]] = field(default_factory=list)
    raw_slots: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    # Slots this item's user has rejected before (P0.4 feedback loop). Derived
    # from rejected Recommendation rows; applied at the FILTER step.
    excluded_slot_ids: frozenset[uuid.UUID] = frozenset()

    pre_filter_count: int = 0
    candidates: list[dict[str, Any]] = field(default_factory=list)
    post_filter_count: int = 0
    ranked_candidates: list[dict[str, Any]] = field(default_factory=list)

    chosen_slot_id: uuid.UUID | None = None
    last_failure: str | None = None
    retries_used: int = 0
    last_decision_reason: str | None = None
    # Only the LLM reasons that passed the no-code/no-English gate, keyed by
    # slot id. Slots the model left unnamed fall back to the deterministic
    # reason attached by ``rank_slots``.
    llm_reasons: dict[str, str] = field(default_factory=dict)

    # For the verifier; built once after retrieval.
    verification_ctx: VerificationContext | None = None

    def step_count(self) -> int:
        """Convenience for tests / logs."""
        return len(self.candidates)


__all__ = ["AgentContext"]
