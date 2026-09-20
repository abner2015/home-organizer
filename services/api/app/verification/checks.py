"""The 9 Verifier checks.

Each function has the signature ``check_*(ctx, candidate) -> CheckResult``
and is pure (no DB, no network). The ``candidate`` is the LLM's chosen
slot dict, which must include the ``id`` field and the parent metadata
injected by ``get_storage_slots`` (room_id, room_type, unit_type, full_path,
etc.).

The Verifier orchestrator runs them in a fixed order and returns the
first failure as the "retry hint" for the LLM.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from app.verification.context import CheckResult, VerificationContext
from app.verification.rule_engine import violates


def _slot(ctx: VerificationContext, slot_id: uuid.UUID) -> dict[str, Any] | None:
    return ctx.slots_by_id.get(slot_id)


def _parse_capacity(capacity_hint: str | None) -> int:
    """Heuristic capacity parser.

    Maps the free-form ``capacity_hint`` column to a small integer:

    - "small"  → 1
    - "medium" → 3
    - "large"  → 6
    - numeric substring → that integer (clamped to [0, 1000])
    - anything else / None → inf

    This is intentionally permissive: an unknown hint means "no cap" rather
    than "zero cap". The check is "active_count ≤ capacity".
    """
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


# --------------------------------------------------------------------- checks


def check_slot_exists(ctx: VerificationContext, candidate: dict[str, Any]) -> CheckResult:
    """Check 1: the candidate slot id is one of the post-filter slot ids."""
    slot_id = uuid.UUID(str(candidate["id"]))
    if slot_id in ctx.known_slot_ids:
        return CheckResult("slot_exists", True, "")
    return CheckResult(
        "slot_exists",
        False,
        f"Slot {slot_id} not in the candidate set",
        details={"candidate_id": str(slot_id)},
    )


def check_slot_belongs_to_home(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 2: the candidate slot belongs to the same home as the item."""
    if candidate.get("home_id") == str(ctx.home_id):
        return CheckResult("slot_belongs_to_home", True, "")
    return CheckResult(
        "slot_belongs_to_home",
        False,
        "Slot belongs to a different home",
        details={"expected_home": str(ctx.home_id), "got_home": candidate.get("home_id")},
    )


def check_capacity(ctx: VerificationContext, candidate: dict[str, Any]) -> CheckResult:
    """Check 3: the slot has room for one more item."""
    slot_id = uuid.UUID(str(candidate["id"]))
    capacity = _parse_capacity(candidate.get("capacity_hint"))
    active = ctx.active_count.get(slot_id, 0)
    if active + 1 <= capacity:
        return CheckResult("capacity", True, "")
    return CheckResult(
        "capacity",
        False,
        f"Slot {candidate.get('code', slot_id)} already holds {active}/{capacity} items",
        details={"active": active, "capacity": capacity},
    )


# A slot counts as "locked" either because its parent unit is a lockable
# container type, or because the section / slot itself is marked as locked.
# The latter matters because a locked drawer commonly lives inside a plain
# cabinet (e.g. 儿童房收纳柜 / 药品带锁抽屉), which would otherwise be
# unreachable for sensitive items.
_LOCKED_UNIT_TYPES = frozenset({"drawer_cabinet", "box"})
_LOCK_MARKERS = ("锁", "locked", "lockable")
_LOCK_WORD = re.compile(r"\block\b")


def _is_locked(candidate: dict[str, Any]) -> bool:
    """True iff the candidate is a locked container.

    Two independent signals, either suffices:

    1. ``unit_type`` is a lockable container type (drawer_cabinet / box).
    2. The section name, slot label, or slot code is marked as locked
       (e.g. "带锁抽屉", "locked drawer").
    """
    if candidate.get("unit_type") in _LOCKED_UNIT_TYPES:
        return True
    text = " ".join(
        str(candidate.get(field) or "")
        for field in ("section_name", "label", "code")
    ).lower()
    if any(marker in text for marker in _LOCK_MARKERS):
        return True
    return bool(_LOCK_WORD.search(text))


def check_hard_safety(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 4: sensitive items must go in a locked container.

    ``is_sensitive=True`` ⇒ the candidate must be locked (see
    :func:`_is_locked`). Failure messages name the accepted signals so the
    LLM can pick a different slot on retry.
    """
    if not ctx.item.get("is_sensitive"):
        return CheckResult("hard_safety", True, "")
    if _is_locked(candidate):
        return CheckResult("hard_safety", True, "")
    return CheckResult(
        "hard_safety",
        False,
        (
            "Sensitive item requires a locked container "
            "(drawer_cabinet / box / a slot marked as locked), got "
            f"unit_type={candidate.get('unit_type')} "
            f"section={candidate.get('section_name')}"
        ),
        details={
            "required_unit_types": sorted(_LOCKED_UNIT_TYPES),
            "got_unit_type": candidate.get("unit_type"),
        },
    )


def check_home_rules(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 5: no hard rule forbids this (candidate, item) pair."""
    for rule in ctx.hard_rules:
        if violates(candidate, rule, ctx.item):
            return CheckResult(
                "home_rules",
                False,
                f"Home rule '{rule.get('name')}' forbids this placement",
                details={"rule_id": rule.get("id"), "rule_name": rule.get("name")},
            )
    return CheckResult("home_rules", True, "")


def check_user_preferences(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 6: user's preferences must not include this slot in avoid list."""
    slot_id = str(candidate["id"])
    for pref in ctx.user_preferences:
        value = pref.get("value") or {}
        if not isinstance(value, dict):
            continue
        avoid = value.get("avoid_slot_ids") or []
        if slot_id in {str(a) for a in avoid}:
            return CheckResult(
                "user_preferences",
                False,
                "User has marked this slot as 'avoid'",
                details={"slot_id": slot_id},
            )
    return CheckResult("user_preferences", True, "")


def check_reason_consistent(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 7: the LLM's reason must reference the item or its path.

    Either:
    - shares at least one token with the item's name / category / subcategory, OR
    - mentions any segment of ``full_path`` (room, unit, section, code).

    The token set is the Unicode word characters in either string. This is
    deliberately permissive (>=1 overlap, not strict containment) because
    the LLM often paraphrases the item name.
    """
    reason = candidate.get("reason") or ""
    if not reason:
        return CheckResult(
            "reason_consistent",
            False,
            "Reason is empty",
        )
    item_tokens = set(_tokens(ctx.item.get("name", ""))) | set(
        _tokens(ctx.item.get("category", ""))
    ) | set(_tokens(ctx.item.get("subcategory", "")))
    reason_tokens = set(_tokens(reason))
    if item_tokens & reason_tokens:
        return CheckResult("reason_consistent", True, "")

    # Fallback: mention of any full_path segment.
    full_path = candidate.get("full_path") or ""
    for segment in full_path.split("/"):
        if segment and segment in reason:
            return CheckResult("reason_consistent", True, "")

    return CheckResult(
        "reason_consistent",
        False,
        "Reason does not mention the item name / category or the slot location",
        details={"reason": reason},
    )


def _tokens(text: str) -> list[str]:
    """Tokenize text into ASCII word-runs + per-character CJK tokens.

    Default ``\\w`` is Unicode-aware, so without the CJK splitting
    "马克杯是常用餐具" would tokenize as one giant run, and
    ``"马克杯" ∩ "马克杯是常用餐具" == ∅``. We pad each CJK character
    with spaces first so each becomes its own token.
    """
    if not text:
        return []
    spaced = re.sub(r"([\u4e00-\u9fff])", r" \1 ", text)
    return [t for t in re.findall(r"\w+", spaced) if t]


# Categories that must not be stored in a bedroom. Deliberately excludes
# medicine: docs/AGENT.md §4.2 maps 药品/保健品 to bedroom/bathroom, and the
# locked-drawer requirement for sensitive medicine is enforced by
# ``check_hard_safety`` instead.
_BEDROOM_FORBIDDEN_CATEGORIES = frozenset({"food", "tool", "chemical"})


def check_no_more_obvious_conflict(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 8: don't put food/tool/chemical in a bedroom.

    Medicine is allowed in a bedroom by design (see
    ``_BEDROOM_FORBIDDEN_CATEGORIES``); sensitive medicine is still
    constrained to a locked container by :func:`check_hard_safety`.
    """
    if candidate.get("room_type") != "bedroom":
        return CheckResult("no_more_obvious_conflict", True, "")
    category = (ctx.item.get("category") or "").lower()
    if category in _BEDROOM_FORBIDDEN_CATEGORIES:
        return CheckResult(
            "no_more_obvious_conflict",
            False,
            f"Item category '{category}' is not appropriate for a bedroom",
            details={"room_type": "bedroom", "category": category},
        )
    return CheckResult("no_more_obvious_conflict", True, "")


def check_no_hallucinated_location(
    ctx: VerificationContext, candidate: dict[str, Any]
) -> CheckResult:
    """Check 9: the slot id must be in the post-filter whitelist."""
    slot_id = uuid.UUID(str(candidate["id"]))
    if slot_id in ctx.whitelist_slot_ids:
        return CheckResult("no_hallucinated_location", True, "")
    return CheckResult(
        "no_hallucinated_location",
        False,
        f"Slot {slot_id} was not in the candidate whitelist (LLM hallucination)",
        details={"candidate_id": str(slot_id)},
    )


# Run order matters: cheap structural checks first (1, 2, 9) so a hallucinated
# id short-circuits before deeper semantic checks fire.
ALL_CHECKS = (
    check_slot_exists,
    check_slot_belongs_to_home,
    check_no_hallucinated_location,
    check_capacity,
    check_hard_safety,
    check_home_rules,
    check_user_preferences,
    check_reason_consistent,
    check_no_more_obvious_conflict,
)


__all__ = [
    "ALL_CHECKS",
    "check_capacity",
    "check_hard_safety",
    "check_home_rules",
    "check_no_hallucinated_location",
    "check_no_more_obvious_conflict",
    "check_reason_consistent",
    "check_slot_belongs_to_home",
    "check_slot_exists",
    "check_user_preferences",
]
