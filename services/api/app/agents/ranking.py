"""Step 7: Deterministic ranker — pure scoring, no LLM.

The score is a weighted sum of independent factors; each is a boolean (0/1)
multiplied by its weight. Higher scores = better match.

    score = 25*category_match + 20*room_match + 15*path_match
          + 15*capacity_fit + 10*preference_match + 10*history_match + 5*soft_rule_match

``room_match`` implements the ``category → room_type`` table from
``docs/AGENT.md`` §4.2: a slot scores when its room's type is one the item's
category belongs in. Before this, the ranker read an ``item["usage_scene"]``
field that no item row has, so every slot scored identically and the LLM
received no ordering signal at all.

Returns the top-20 candidates sorted by descending score. Ties break on
``full_path`` (then ``id``) so the output is fully deterministic, as §4.4
requires. The LLM rank step picks from this truncated list (the "top
candidates" payload). Each row also carries ``score_terms`` (the 0/1 breakdown
above) and a deterministic Chinese ``reason`` built from it (P0.4).
"""
from __future__ import annotations

from typing import Any

from app.agents.reason import build_reason

# docs/AGENT.md §4.2 — built-in cold-start table mapping an item category to
# the room types it belongs in. The doc's rows are in the domain vocabulary
# (Chinese, see docs/DOMAIN.md §Item); the English keys are the synonyms the
# vision layer (``DEFAULT_ALLOWED_CATEGORIES``) and the eval dataset emit.
# A category absent from the table gets no room signal (0), which is honest:
# nothing is known about where it belongs.
_CATEGORY_ROOM_TYPES: dict[str, frozenset[str]] = {
    # --- docs/AGENT.md §4.2 rows ---
    "厨具": frozenset({"kitchen"}),
    "餐具": frozenset({"kitchen"}),
    "食品": frozenset({"kitchen", "living"}),
    "衣物": frozenset({"bedroom"}),
    "鞋帽": frozenset({"bedroom"}),
    "工具": frozenset({"living", "storage", "other"}),
    "五金": frozenset({"storage", "other"}),
    "书籍": frozenset({"study", "living"}),
    "药品": frozenset({"bedroom", "bathroom"}),
    "保健品": frozenset({"bedroom", "bathroom"}),
    "电子产品": frozenset({"study", "living"}),
    "玩具": frozenset({"living", "bedroom"}),
    "文档": frozenset({"study"}),
    "证件": frozenset({"study"}),
    # --- English equivalents / categories the dataset uses ---
    "utensil": frozenset({"kitchen"}),
    "food": frozenset({"kitchen", "living"}),
    "medicine": frozenset({"bedroom", "bathroom"}),
    "tool": frozenset({"living", "storage", "other"}),
    "hardware": frozenset({"storage", "other"}),
    "book": frozenset({"study", "living"}),
    "books": frozenset({"study", "living"}),
    "electronic": frozenset({"study", "living"}),
    "electronics": frozenset({"study", "living"}),
    "toy": frozenset({"living", "bedroom"}),
    "toys": frozenset({"living", "bedroom"}),
    "clothes": frozenset({"bedroom"}),
    "clothing": frozenset({"bedroom"}),
    "cleaning": frozenset({"kitchen", "bathroom"}),
    "chemical": frozenset({"kitchen", "bathroom", "storage"}),
    "fragile": frozenset({"living", "kitchen"}),
    "glass": frozenset({"living", "kitchen"}),
    "ceramic": frozenset({"living", "kitchen"}),
    "decor": frozenset({"living"}),
    "daily": frozenset({"living", "kitchen"}),
    "travel": frozenset({"living", "bedroom"}),
    "camping": frozenset({"living", "bedroom", "storage"}),
    "appliance": frozenset({"kitchen", "living"}),
    "kids": frozenset({"bedroom"}),
    "document": frozenset({"study"}),
    "documents": frozenset({"study"}),
}


def _category_match(slot: dict[str, Any], item: dict[str, Any]) -> int:
    allowed = slot.get("allowed_categories") or []
    category = (item.get("category") or "").lower()
    if not allowed:
        return 1  # open slot; mild positive signal
    return 1 if category in {a.lower() for a in allowed} else 0


def _preferred_room_types(item: dict[str, Any]) -> frozenset[str]:
    """Room types the item's category belongs in, per docs/AGENT.md §4.2."""
    category = (item.get("category") or "").strip().lower()
    if not category:
        return frozenset()
    return _CATEGORY_ROOM_TYPES.get(category, frozenset())


def _room_match(slot: dict[str, Any], item: dict[str, Any]) -> int:
    """1 if the slot's room type is one the item's category belongs in.

    §4.1 also describes decay for non-matching rooms; the repo has no room
    adjacency model, so this stays binary (match = 1, everything else = 0).
    """
    room_type = (slot.get("room_type") or "").strip().lower()
    if not room_type:
        return 0
    return 1 if room_type in _preferred_room_types(item) else 0


def _path_match(slot: dict[str, Any], item: dict[str, Any]) -> int:
    """Substring match of the item name against any full_path segment."""
    name = (item.get("name") or "").lower()
    if not name:
        return 0
    parts = (slot.get("full_path") or "").lower().split("/")
    return 1 if any(name in p or p in name for p in parts if p) else 0


def _capacity_fit(slot: dict[str, Any]) -> int:
    """1 if the slot has clear room (active_count == 0), else 0."""
    return 1 if (slot.get("active_count") or 0) == 0 else 0


def _preference_match(
    slot: dict[str, Any], item: dict[str, Any], preferences: list[dict[str, Any]]
) -> int:
    """1 if a stored positive preference covers this slot for this item.

    Preferences are written by the feedback loop (P0.4) as
    ``{"slots": {"<slot_id>": {"category": ..., "count": ...}}}`` and are
    **category-scoped**: accepting a slot for a mug must not boost it for
    medicine. A preference with no stored category matches any item, and the
    legacy ``preferred_slot_ids`` shape (no category at all) is still honoured.
    """
    sid = str(slot["id"])
    category = (item.get("category") or "").strip().lower()
    for pref in preferences:
        value = pref.get("value") or {}
        if not isinstance(value, dict):
            continue
        slots = value.get("slots")
        if isinstance(slots, dict):
            entry = slots.get(sid)
            if isinstance(entry, dict):
                entry_category = str(entry.get("category") or "").strip().lower()
                if not entry_category or entry_category == category:
                    return 1
        legacy = value.get("preferred_slot_ids") or []
        if sid in {str(a) for a in legacy}:
            return 1
    return 0


def _history_match(slot: dict[str, Any], history: list[dict[str, Any]]) -> int:
    """1 if the item has previously been placed in this slot (or a slot in
    the same section)."""
    sid = str(slot["id"])
    section_id = str(slot.get("section_id") or "")
    for h in history:
        if str(h.get("slot_id")) == sid:
            return 1
        # Same section counts as a mild historical hint.
        # (We don't have the section id of past placements in the dict, so
        # skip the section heuristic for now — keep this simple.)
    _ = section_id
    return 0


def _soft_rule_match(slot: dict[str, Any], soft_rules: list[dict[str, Any]]) -> int:
    """1 if any soft rule's positive phrasing mentions the slot's room/unit."""
    text = (
        (slot.get("room_name") or "")
        + " "
        + (slot.get("unit_name") or "")
        + " "
        + (slot.get("section_name") or "")
    ).lower()
    for rule in soft_rules:
        name = (rule.get("name") or "").lower()
        if not name:
            continue
        # Positive soft rules contain "靠近" / "常用" / "near" etc.
        if any(marker in name for marker in ("靠近", "常用", "near", "close", "常用物品")):
            for word in ("餐桌", "沙发", "床", "门口", "厨房", "浴室", "书桌", "工作区"):
                if word in name and word in text:
                    return 1
    return 0


# Weight per scoring term. Kept as data (not inlined into the sum) so the
# score can be broken into its parts — the reason builder reads those parts.
_WEIGHTS: dict[str, int] = {
    "category": 25,
    "room": 20,
    "path": 15,
    "capacity": 15,
    "preference": 10,
    "history": 10,
    "soft_rule": 5,
}


def score_terms(
    slot: dict[str, Any],
    *,
    item: dict[str, Any],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    soft_rules: list[dict[str, Any]],
) -> dict[str, int]:
    """The per-term 0/1 breakdown behind :func:`deterministic_score`."""
    return {
        "category": _category_match(slot, item),
        "room": _room_match(slot, item),
        "path": _path_match(slot, item),
        "capacity": _capacity_fit(slot),
        "preference": _preference_match(slot, item, preferences),
        "history": _history_match(slot, history),
        "soft_rule": _soft_rule_match(slot, soft_rules),
    }


def deterministic_score(
    slot: dict[str, Any],
    *,
    item: dict[str, Any],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    soft_rules: list[dict[str, Any]],
) -> int:
    """Return the weighted score for one slot."""
    terms = score_terms(
        slot,
        item=item,
        preferences=preferences,
        history=history,
        soft_rules=soft_rules,
    )
    return sum(_WEIGHTS[term] * value for term, value in terms.items())


def rank_slots(
    candidates: list[dict[str, Any]],
    *,
    item: dict[str, Any],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    soft_rules: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return ``candidates`` sorted by descending score; each row gains a
    ``det_score`` field. Truncated to ``limit`` rows for the LLM prompt.

    Equally-scored slots break on ``full_path`` so the result does not depend
    on the DB's row order (``get_storage_slots`` orders by ``sort_order, code``,
    which ties for many real and synthetic slots). §4.4 requires the ranker to
    be fully deterministic.
    """
    scored = []
    for c in candidates:
        terms = score_terms(
            c,
            item=item,
            preferences=preferences,
            history=history,
            soft_rules=soft_rules,
        )
        score = sum(_WEIGHTS[term] * value for term, value in terms.items())
        scored.append((score, terms, c))
    scored.sort(
        key=lambda triple: (
            -triple[0],
            str(triple[2].get("full_path") or ""),
            str(triple[2].get("id") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    for score, terms, c in scored[:limit]:
        # Every ranked slot carries a deterministic reason. The LLM may later
        # overlay a better one for the candidates it actually names (pipeline
        # DECIDE step); anything it does not name — and the whole no-LLM
        # `GET /items/{id}/candidates` path — keeps this one.
        out.append(
            {
                **c,
                "det_score": score,
                "score_terms": terms,
                "reason": build_reason(c, item, score_terms=terms),
            }
        )
    return out


__all__ = ["deterministic_score", "rank_slots", "score_terms"]
