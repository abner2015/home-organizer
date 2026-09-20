"""Steps 5 + 6 of the pipeline: Candidate Generation + Hard Constraint Filtering.

Both are pure functions over an already-fetched slot list. The agent
calls them in order:

1. ``generate_candidates(slots, item)`` — coarse applicability gate.
2. ``hard_filter(candidates, ctx)`` — apply capacity, sensitivity, hard rules.

The output of hard_filter is what the LLM sees (truncated by the ranker).
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.verification.rule_engine import violates

# A small allowlist of categories; not used by the gate itself but exposed
# here so callers can extend it later (e.g. project-specific synonyms).
DEFAULT_ALLOWED_CATEGORIES: tuple[str, ...] = (
    "utensil",
    "food",
    "medicine",
    "tool",
    "chemical",
    "decor",
    "books",
    "glass",
    "ceramic",
    "misc",
)


def _category_matches(slot_allowed: list[str], item_category: str | None) -> bool:
    """True iff the slot's allowed_categories list is empty OR includes the item category."""
    if not slot_allowed:
        return True
    if not item_category:
        return True
    lowered = {a.lower() for a in slot_allowed}
    return item_category.lower() in lowered


def _size_fits(slot: dict[str, Any], item: dict[str, Any]) -> bool:
    """Very coarse size gate from the slot's capacity_hint.

    - "small" slot ⇒ item.estimated_size in {"small", ""}
    - "medium" slot ⇒ in {"small", "medium", ""}
    - "large" slot ⇒ always fits.
    Numeric hints: items whose estimated_size matches "small/medium/large"
    pass when ≤ hint's bucket.
    """
    cap = (slot.get("capacity_hint") or "").strip().lower()
    item_size = (item.get("estimated_size") or "").strip().lower()
    if cap in {"large", "大", "大量"}:
        return True
    if cap in {"medium", "中", "中等"}:
        return item_size in {"", "small", "medium"}
    if cap in {"small", "小", "少量"}:
        return item_size in {"", "small"}
    # Unknown / numeric — be permissive.
    return True


def generate_candidates(
    slots: list[dict[str, Any]], item: dict[str, Any]
) -> list[dict[str, Any]]:
    """Step 5: coarse gate (allowed_categories + size). Returns dicts in-place
    plus an ``_applicable`` marker so the next step can reason about why.
    """
    out: list[dict[str, Any]] = []
    for slot in slots:
        if not _category_matches(slot.get("allowed_categories", []), item.get("category")):
            continue
        if not _size_fits(slot, item):
            continue
        out.append({**slot, "_applicable": True})
    return out


def _parse_capacity(capacity_hint: str | None) -> int:
    if not capacity_hint:
        return 10**9
    text = capacity_hint.strip().lower()
    if text in {"small", "小", "少量"}:
        return 1
    if text in {"medium", "中", "中等"}:
        return 3
    if text in {"large", "大", "大量"}:
        return 6
    m = re.search(r"\d+", text)
    if m:
        return max(0, min(int(m.group(0)), 10**9))
    return 10**9


def hard_filter(
    candidates: list[dict[str, Any]],
    *,
    item: dict[str, Any],
    hard_rules: list[dict[str, Any]],
    active_count: dict[uuid.UUID, int],
) -> list[dict[str, Any]]:
    """Step 6: remove candidates that violate hard rules.

    Capacity and safety checks live in the Verifier (steps 3 & 4) so a
    verifier failure triggers a retry; if they ran here, an over-full or
    unsafe slot would never reach the LLM and the retry mechanism would be
    useless.
    """
    out: list[dict[str, Any]] = []
    for c in candidates:
        # Hard rules: any matching denial disqualifies.
        if any(violates(c, rule, item) for rule in hard_rules):
            continue
        out.append(c)
    return out


__all__ = ["DEFAULT_ALLOWED_CATEGORIES", "generate_candidates", "hard_filter"]
