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
candidates" payload).
"""
from __future__ import annotations

from typing import Any

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


def _preference_match(slot: dict[str, Any], preferences: list[dict[str, Any]]) -> int:
    sid = str(slot["id"])
    for pref in preferences:
        value = pref.get("value") or {}
        if not isinstance(value, dict):
            continue
        preferred = value.get("preferred_slot_ids") or []
        if sid in {str(a) for a in preferred}:
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


def deterministic_score(
    slot: dict[str, Any],
    *,
    item: dict[str, Any],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    soft_rules: list[dict[str, Any]],
) -> int:
    """Return the weighted score for one slot."""
    return (
        25 * _category_match(slot, item)
        + 20 * _room_match(slot, item)
        + 15 * _path_match(slot, item)
        + 15 * _capacity_fit(slot)
        + 10 * _preference_match(slot, preferences)
        + 10 * _history_match(slot, history)
        + 5 * _soft_rule_match(slot, soft_rules)
    )


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
    scored = [
        (
            deterministic_score(
                c,
                item=item,
                preferences=preferences,
                history=history,
                soft_rules=soft_rules,
            ),
            c,
        )
        for c in candidates
    ]
    scored.sort(
        key=lambda pair: (
            -pair[0],
            str(pair[1].get("full_path") or ""),
            str(pair[1].get("id") or ""),
        )
    )
    out: list[dict[str, Any]] = []
    for score, c in scored[:limit]:
        out.append({**c, "det_score": score})
    return out


__all__ = ["deterministic_score", "rank_slots"]
