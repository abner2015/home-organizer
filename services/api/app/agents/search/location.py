"""Location-hint matching for the search agent's FIND_LOCATION flow.

The LLM extracts a ``location_hint`` from the user's Chinese sentence, and it
comes back as the phrase the user typed — ``"客厅装饰柜 L1"`` — while a slot's
``full_path`` is ``"客厅/客厅装饰柜/左玻璃柜/L1"``. A naive ``hint in
full_path`` substring test fails on the space and the slashes, so the assistant
used to answer "客厅装饰柜 L1里没有放置任何物品" for a slot that had an item in
it. This module replaces that test.

Both functions are pure (no DB, no LLM) so they can be unit-tested directly.
"""
from __future__ import annotations

import re
from typing import Any

# Real separators a hint may use between its parts. Splitting on these (rather
# than on character classes) is what keeps "第1层" a single indivisible token —
# splitting it into 第/1/层 would make it spuriously match "第2层" via the "1"
# inside that slot's own code.
_SEPARATORS = re.compile(r"[\s/、,，。;；|\\-]+")

# A slot code always starts with a letter (``L1``, ``L1S1``, ``D1``). Matching it
# as a *trailing* run also handles the unspaced form "客厅装饰柜L1".
_TRAILING_CODE = re.compile(r"^(?P<name>.*?)(?P<code>[A-Za-z][A-Za-z0-9_]*)$")

# Ordering tiers — lower is stronger.
_TIER_EXACT_CODE = 0
_TIER_UNIT = 1
_TIER_SECTION = 2
_TIER_ROOM = 3
_TIER_PARTIAL = 4


def _parse(hint: str) -> tuple[str, list[str]]:
    """``(code_token, name_fragments)`` for a location hint.

    ``"客厅装饰柜 L1"`` / ``"客厅装饰柜L1"`` -> ``("L1", ["客厅装饰柜"])``
    ``"L1"``                                -> ``("L1", [])``
    ``"第1层"``                             -> ``("", ["第1层"])``
    ``"客厅/客厅装饰柜/左玻璃柜/L1"``        -> ``("L1", ["客厅", "客厅装饰柜", "左玻璃柜"])``

    Only the FIRST code-looking token is taken as the code; anything else is a
    name fragment that must also appear in the matched slot.
    """
    stripped = hint.strip() if hint else ""
    if not stripped:
        return "", []
    code = ""
    names: list[str] = []
    for token in _SEPARATORS.split(stripped):
        if not token:
            continue
        found = _TRAILING_CODE.match(token)
        if found is not None:
            token_name = found.group("name")
            if token_name:
                names.append(token_name)
            if not code:
                code = found.group("code")
            else:
                # A second code-looking token: keep it as a name fragment
                # rather than silently dropping part of what the user said.
                names.append(found.group("code"))
        else:
            names.append(token)
    return code, names


def split_location_hint(hint: str) -> tuple[str, str]:
    """Split a location hint into ``(code_token, name_phrase)``.

    The public single-string form of :func:`_parse`, kept because it is the
    natural shape for logging and tests.
    """
    code, names = _parse(hint)
    return code, "".join(names)


def _haystack(slot: dict[str, Any]) -> str:
    """Everything about a slot a human might name, lower-cased.

    Includes the individual room/unit/section names as well as ``full_path``
    because a user may name any level ("厨房", "厨房吊柜", "第1层").
    """
    parts = (
        slot.get("full_path"),
        slot.get("room_name"),
        slot.get("unit_name"),
        slot.get("section_name"),
        slot.get("label"),
        slot.get("code"),
    )
    return " ".join(str(p) for p in parts if p).lower()


def _matches(slot: dict[str, Any], *, code: str, names: list[str]) -> bool:
    """AND of the available constraints; nothing to match on means no match."""
    if not code and not names:
        return False
    slot_code = str(slot.get("code") or "").lower()
    if code and not slot_code.startswith(code.lower()):
        # One-directional: the hint's code is a prefix of the slot's code, so
        # "L1" reaches L1S1/L1S2 while "L1S1" does not reach L1.
        return False
    if names:
        haystack = _haystack(slot)
        return all(name.lower() in haystack for name in names)
    return True


def _order_key(
    slot: dict[str, Any], *, code: str, names: list[str]
) -> tuple[int, str, str]:
    """Rank matched slots, strongest first.

    An exact code hit beats a prefix hit (``L1`` before ``L1S1``), then a hint
    that names a whole unit/section/room ranks its slots together. The final
    two keys mirror :func:`app.agents.ranking.rank_slots`' tie-break so the
    answer is deterministic across runs.
    """
    code_l = code.lower()
    lowered = [n.lower() for n in names]

    def _names(level: str) -> bool:
        return str(slot.get(level) or "").lower() in lowered

    slot_code = str(slot.get("code") or "").lower()
    if code and slot_code == code_l:
        tier = _TIER_EXACT_CODE
    elif _names("unit_name"):
        tier = _TIER_UNIT
    elif _names("section_name"):
        tier = _TIER_SECTION
    elif _names("room_name"):
        tier = _TIER_ROOM
    else:
        tier = _TIER_PARTIAL
    return (tier, str(slot.get("full_path") or ""), str(slot.get("id") or ""))


def match_slots_by_hint(slots: list[dict[str, Any]], hint: str) -> list[dict[str, Any]]:
    """Return the slots a location hint refers to, strongest first.

    Naming a whole unit ("客厅装饰柜") returns every slot under it; combining a
    name with a code ("客厅装饰柜 L1") narrows to that slot and, critically,
    excludes a same-coded slot in another room.
    """
    code, names = _parse(hint)
    if not code and not names:
        return []
    matched = [s for s in slots if _matches(s, code=code, names=names)]
    matched.sort(key=lambda s: _order_key(s, code=code, names=names))
    return matched


__all__ = ["match_slots_by_hint", "split_location_hint"]
