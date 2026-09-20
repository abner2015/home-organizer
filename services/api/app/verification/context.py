"""Dataclasses for the Verifier — no I/O, no DB.

The orchestrator builds one :class:`VerificationContext` per pipeline run
from the read-only tool results. Checks read from it and emit
:class:`CheckResult` values; the verifier aggregates them into a
:class:`VerificationResult`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class CheckResult:
    """Outcome of a single check function."""

    code: str
    passed: bool
    message: str
    # Optional payload with structured details (e.g. rule_id that triggered).
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class VerificationContext:
    """All data the 9 checks need to evaluate one candidate decision.

    The orchestrator is responsible for populating this from the DB / tool
    calls BEFORE invoking the LLM Decision step. The Verifier itself must
    remain pure (no DB access) so it can be unit-tested with dicts only.

    Required keys:

    - ``home_id``: the home the recommendation belongs to.
    - ``known_slot_ids``: set of slot ids that exist in the home (post-filter).
    - ``whitelist_slot_ids``: same as ``known_slot_ids`` for now — alias used
      by check #9 ``no_hallucinated_location``.
    - ``slots_by_id``: id → enriched slot dict from ``get_storage_slots``.
    - ``active_count``: slot id → count of active placements (post-filter).
    - ``item``: the Item dict (name, category, subcategory, is_sensitive,
      needs_lock, estimated_size).
    - ``hard_rules``: list of ``HomeRule`` dicts with ``rule_type='hard'``
      and ``enabled=True``.
    - ``user_preferences``: list of ``UserPreference`` dicts for the user.
    - ``history_items``: list of dicts from ``get_item_placements`` for the
      item, used for "history_match" scoring / hint.
    """

    home_id: uuid.UUID
    known_slot_ids: frozenset[uuid.UUID]
    whitelist_slot_ids: frozenset[uuid.UUID]
    slots_by_id: dict[uuid.UUID, dict[str, Any]]
    active_count: dict[uuid.UUID, int]
    item: dict[str, Any]
    hard_rules: tuple[dict[str, Any], ...]
    user_preferences: tuple[dict[str, Any], ...]
    history_items: tuple[dict[str, Any], ...] = ()


@dataclass(slots=True, frozen=True)
class VerificationResult:
    """Aggregated verifier output.

    ``ok`` is True iff every check passed. ``failed`` contains the failed
    checks in the order they were run; ``message`` is the first failure
    message (used by the orchestrator to feed ``last_failure`` to the LLM
    on retry).
    """

    ok: bool
    failed: tuple[CheckResult, ...]
    passed: tuple[CheckResult, ...] = ()

    @property
    def message(self) -> str:
        return self.failed[0].message if self.failed else ""


__all__ = ["CheckResult", "VerificationContext", "VerificationResult"]
