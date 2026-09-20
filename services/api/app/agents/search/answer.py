"""Pure answer-formatters for the search agent (Phase 6).

Each helper takes a list of *candidate dicts* (see :func:`SearchAgent.run`
for shape) and an intent, and returns a Chinese sentence the UI can
display verbatim.

These helpers never touch the database, the LLM, or any side-effecting
state. They are deliberately small and pure so the agent tests can
exercise them in isolation.

Shape contract — ``candidate``::

    {
        "item_id":  uuid.UUID,
        "name":     str,
        "category": str,
        "subcategory": str,
        "is_sensitive": bool,
        "location": {                              # or None if not placed
            "slot_id":     uuid.UUID,
            "code":        str,
            "label":       str,
            "full_path":   str,
            "room_name":   str,
            "unit_name":   str,
            "section_name": str,
        } | None,
    }
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.agents.search.intent import ExtractedSearchIntent, SearchIntentKind

# ---------------------------------------------------------------------- helpers


def _location_text(loc: dict[str, Any] | None) -> str:
    """Format a single candidate's location as a Chinese path string.

    Falls back to "未放置" for unplaced items.
    """
    if not loc:
        return "未放置"
    return loc.get("full_path") or "未放置"


def _placed(c: dict[str, Any]) -> bool:
    return bool(c.get("location"))


# ---------------------------------------------------------------------- per-intent formatters


def format_find_item(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format FIND_ITEM answer.

    Returns ``(state, answer_text)`` where ``state`` is one of:

    - ``"answer"`` — exactly one match, the item is placed.
    - ``"exists_but_not_placed"`` — exactly one match but the item has no
      active placement (we can say "家里有X但是还没放好").
    - ``"needs_clarification"`` — multiple matches, list all + ask which.
    - ``"not_found"`` — zero matches.
    """
    if len(candidates) == 0:
        return ("not_found", f"没有找到{intent.query or '该物品'}。")
    if len(candidates) == 1:
        c = candidates[0]
        if _placed(c):
            return (
                "answer",
                f"{c['name']}在 {_location_text(c['location'])}。",
            )
        return (
            "exists_but_not_placed",
            f"家里有{c['name']}，但是还没有放置位置。",
        )
    # Multiple matches → ask the user to pick.
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        if _placed(c):
            lines.append(f"{i}) {c['name']}（{_location_text(c['location'])}）")
        else:
            lines.append(f"{i}) {c['name']}（未放置）")
    body = "；".join(lines)
    return (
        "needs_clarification",
        f"找到多个匹配：{body}。请问您要找的是哪一个？",
    )


def format_find_items(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format FIND_ITEMS answer — always a list, never a clarification."""
    if not candidates:
        # Try to be location-hint-aware in the negative case.
        if intent.location_hint:
            return (
                "not_found",
                f"{intent.location_hint}里没有找到任何「{intent.query or '物品'}」。",
            )
        return ("not_found", f"没有找到「{intent.query or '物品'}」。")
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        if _placed(c):
            lines.append(f"{i}) {c['name']}（{_location_text(c['location'])}）")
        else:
            lines.append(f"{i}) {c['name']}（未放置）")
    body = "；".join(lines)
    head = f"找到了 {len(candidates)} 个匹配"
    if intent.location_hint:
        head = f"在{intent.location_hint}找到了 {len(candidates)} 个匹配"
    return ("answer", f"{head}：{body}。")


def format_find_location(
    intent: ExtractedSearchIntent,
    candidates: list[dict[str, Any]],
    slot_label: str = "",
) -> tuple[str, str]:
    """Format FIND_LOCATION answer — items placed in a named slot/section."""
    if not candidates:
        if slot_label:
            return ("not_found", f"{slot_label}里没有放置任何物品。")
        if intent.location_hint:
            return ("not_found", f"「{intent.location_hint}」里没有放置任何物品。")
        return ("not_found", "该位置没有放置任何物品。")
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        # For find_location we already know each item is placed in the slot;
        # show the item name + its room path for context.
        if c.get("location"):
            lines.append(f"{i}) {c['name']}")
        else:
            lines.append(f"{i}) {c['name']}")
    body = "、".join(lines)
    head = f"{slot_label or intent.location_hint or '该位置'}里放置了：{body}"
    return ("answer", head + "。")


def format_check_existence(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format CHECK_EXISTENCE answer — yes/no + optional location of the first match."""
    if not candidates:
        return ("not_found", f"家里没有{intent.query or '该物品'}。")
    n = len(candidates)
    first = candidates[0]
    head = f"有 {n} 个{intent.query or '物品'}"
    if n == 1:
        head = f"有 1 个{intent.query or '物品'}"
    tail = f"（{_location_text(first['location'])}）" if _placed(first) else "（未放置）"
    if n == 1:
        return ("answer", f"{head}{tail}。")
    # Multiple — mention total + first.
    return ("answer", f"{head}{tail}，还有 {n - 1} 个。")


def format_list_category(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format LIST_CATEGORY answer — group items by room when possible."""
    if not candidates:
        return ("not_found", f"家里没有「{intent.category or '该类别'}」的物品。")
    by_room: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        loc = c.get("location") or {}
        room = loc.get("room_name") or "未放置"
        by_room.setdefault(room, []).append(c)
    parts: list[str] = []
    # Stable iteration order: rooms with placed items first (alphabetical),
    # "未放置" last so the user always sees the answerable info first.
    placed_rooms = sorted(r for r in by_room if r != "未放置")
    if "未放置" in by_room:
        placed_rooms.append("未放置")
    for room in placed_rooms:
        items_here = by_room[room]
        names = "、".join(c["name"] for c in items_here)
        parts.append(f"{room}：{names}")
    body = "；".join(parts)
    head = f"家里共有 {len(candidates)} 件「{intent.category or '该类别'}」物品"
    return ("answer", f"{head}：{body}。")


# ---------------------------------------------------------------------- entry-point


def format_answer(
    intent: ExtractedSearchIntent,
    candidates: Sequence[dict[str, Any]],
    *,
    slot_label: str = "",
) -> tuple[str, str]:
    """Dispatch to the per-intent formatter and return ``(state, text)``.

    ``slot_label`` is an optional pre-computed label used by
    :func:`format_find_location` when the slot the agent matched on has
    a nicely formatted Chinese path (e.g. "客厅装饰柜L1").
    """
    cands = list(candidates)
    kind = intent.intent
    if kind == SearchIntentKind.FIND_ITEM:
        return format_find_item(intent, cands)
    if kind == SearchIntentKind.FIND_ITEMS:
        return format_find_items(intent, cands)
    if kind == SearchIntentKind.FIND_LOCATION:
        return format_find_location(intent, cands, slot_label=slot_label)
    if kind == SearchIntentKind.CHECK_EXISTENCE:
        return format_check_existence(intent, cands)
    if kind == SearchIntentKind.LIST_CATEGORY:
        return format_list_category(intent, cands)
    # UNKNOWN / fallback — caller passes the question through.
    question = intent.question or "请告诉我您想找什么物品。"
    return ("needs_clarification", question)


__all__ = [
    "format_answer",
    "format_check_existence",
    "format_find_item",
    "format_find_items",
    "format_find_location",
    "format_list_category",
]
