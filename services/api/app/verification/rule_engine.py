"""Rule scope matcher — used by both the Hard-Filter step and the Verifier.

A rule's ``scope`` (``docs/DOMAIN.md`` §6, evaluated per ``docs/AGENT.md`` §5.2)
has two halves:

* **item filters** — which items the rule is *about*: ``categories`` /
  ``item_categories``, ``needs_lock``, ``is_sensitive``.
* **requirements** — what must then hold for the placement: ``room_types`` /
  ``slot_room_types``, ``unit_types`` / ``slot_unit_types``, ``room_id``,
  ``requires_attributes``, ``forbids_attributes``.

So ``{"categories": ["food"], "room_types": ["kitchen"]}`` reads "food belongs
in the kitchen": a food item in a bedroom breaches it, a food item in the
kitchen does not, and an unrelated item is not covered at all.

Polarity therefore comes from the scope's structure, never from the rule's
prose. The previous implementation scanned ``name + description`` for negation
keywords, which inverted every positively-worded rule: the seed rule
``厨房不放过期食品`` (scope: kitchen + food) contains 不 and 不允许, so it
dropped *every kitchen slot* for every food item — the exact opposite of its
intent.

Scope keys that are not recognised are ignored rather than guessed at; they can
neither satisfy nor breach a rule.
"""
from __future__ import annotations

from typing import Any

# Item filters: the rule only covers items matching these.
_CATEGORY_KEYS = ("item_categories", "categories")
# Requirements: placements of a covered item must satisfy all of these.
_ROOM_KEYS = ("slot_room_types", "room_types")
_UNIT_KEYS = ("slot_unit_types", "unit_types")

_LOCK_FILTER_KEYS = ("needs_lock", "is_sensitive")


def _match_category(item: dict[str, Any], scope: dict[str, Any]) -> bool:
    """True iff the item's category is one the rule covers (or it names none)."""
    for key in _CATEGORY_KEYS:
        if key not in scope:
            continue
        category = (item.get("category") or "").lower()
        if not any(str(s).lower() == category for s in scope[key]):
            return False
    return True


def _match_flags(item: dict[str, Any], scope: dict[str, Any]) -> bool:
    """True iff the item's needs_lock / is_sensitive flags match the rule."""
    for key in _LOCK_FILTER_KEYS:
        if key in scope and bool(item.get(key)) != bool(scope[key]):
            return False
    return True


def applies(scope: dict[str, Any], item: dict[str, Any]) -> bool:
    """True iff the rule is about this item.

    An empty scope covers everything.
    """
    if not scope:
        return True
    return _match_category(item, scope) and _match_flags(item, scope)


def _requirements_met(
    scope: dict[str, Any], candidate: dict[str, Any], item: dict[str, Any]
) -> bool:
    """True iff a covered placement satisfies every requirement in the scope."""
    for key in _ROOM_KEYS:
        if key in scope and candidate.get("room_type") not in scope[key]:
            return False
    for key in _UNIT_KEYS:
        if key in scope and candidate.get("unit_type") not in scope[key]:
            return False
    if "room_id" in scope and candidate.get("room_id") != scope["room_id"]:
        return False
    if not all(item.get(attr) for attr in scope.get("requires_attributes") or []):
        return False
    return not any(
        item.get(attr) for attr in scope.get("forbids_attributes") or []
    )


def violates(
    candidate: dict[str, Any],
    rule: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """True iff this hard rule is breached by placing ``item`` in ``candidate``.

    A breach is a *failed requirement*: the rule covers the item
    (:func:`applies`) but the placement does not satisfy the scope. A rule that
    merely mentions a forbidden-sounding word in its description is not a
    breach.
    """
    scope = rule.get("scope") or {}
    if not applies(scope, item):
        return False
    return not _requirements_met(scope, candidate, item)


__all__ = ["applies", "violates"]
