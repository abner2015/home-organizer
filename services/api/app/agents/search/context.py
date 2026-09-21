"""Render the user's real home into a compact block for the search prompt.

Without this the intent-extraction LLM had to guess at vocabulary, and it
guessed wrong: asked 「我家所有的厨房用品？」 it emitted ``category="kitchen"``,
but ``kitchen`` is a *room type* and :func:`app.tools.item_tools.search_items`
filters on ``Item.category == category`` — an exact match against values like
``utensil`` / ``food``. Zero rows came back and the assistant confidently told
the user the home had no kitchen items.

The category vocabulary below is therefore derived from the caller's own data
(the union of every item's ``category`` and every slot's ``allowed_categories``)
rather than hard-coded from any of the four disagreeing lists in the repo.

Pure function: no DB, no LLM. Stable output for a given input so the recorded
``prompt_hash`` stays comparable across runs.
"""
from __future__ import annotations

from typing import Any

from app.agents.search.structure import UNIT_TYPE_LABEL

# A real home can have hundreds of slots; the prompt must not grow with it.
MAX_SLOT_LINES = 60
MAX_ITEM_NAMES = 25


def _rooms(slots: list[dict[str, Any]]) -> list[str]:
    """Distinct room names, in the order the slots come back."""
    seen: list[str] = []
    for slot in slots:
        name = str(slot.get("room_name") or "").strip()
        if name and name not in seen:
            seen.append(name)
    return seen


def _categories(slots: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[str]:
    """Sorted union of item categories and slot allowed_categories."""
    seen: set[str] = set()
    for item in items:
        value = item.get("category")
        if value:
            seen.add(str(value).strip().lower())
    for slot in slots:
        for value in slot.get("allowed_categories") or []:
            if value:
                seen.add(str(value).strip().lower())
    seen.discard("")
    return sorted(seen)


def _structure_summary(slots: list[dict[str, Any]]) -> str:
    """One line describing the home's storage structure, derived from slots.

    Without it the model could not see that a structure question was even
    answerable: asked 「我家一共有几个房间？」 it fell back to ``unknown`` and told
    the user it had no way to count rooms. Rooms are ``room_name``s the slots
    already carry, and ``unit_type`` gives the furniture mix (「几个柜子」).

    Derived from slots rather than from ``get_rooms`` / ``get_storage_units`` so
    the prompt block stays a single query's worth of input. The consequence is
    that a storage unit holding no positions is invisible here — the
    ``describe_storage`` handler is the authority on exact counts.
    """
    if not slots:
        return "收纳结构：家里还没有任何收纳位置。"

    rooms: set[str] = set()
    section_ids: set[str] = set()
    unit_id_to_type: dict[str, str] = {}
    placed = 0
    for slot in slots:
        room = str(slot.get("room_name") or "").strip()
        if room:
            rooms.add(room)
        section_ids.add(str(slot.get("section_id") or ""))
        unit_id = str(slot.get("unit_id") or "")
        if unit_id:
            unit_id_to_type[unit_id] = str(slot.get("unit_type") or "").strip().lower()
        placed += int(slot.get("active_count") or 0)

    type_counts: dict[str, int] = {}
    for unit_type in unit_id_to_type.values():
        type_counts[unit_type] = type_counts.get(unit_type, 0) + 1
    by_type = "、".join(
        f"{UNIT_TYPE_LABEL.get(t, t)} {n} 件"
        for t, n in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    # Room *names* are already on the line above; this line carries the counts.
    return (
        f"收纳结构：房间 {len(rooms)} 个；"
        f"收纳家具 {len(unit_id_to_type)} 件（{by_type}）；"
        f"收纳分区 {len(section_ids)} 处；收纳位 {len(slots)} 个，其中 {placed} 个已放物品。"
    )


def build_home_context(
    *,
    slots: list[dict[str, Any]],
    items: list[dict[str, Any]],
    max_slots: int = MAX_SLOT_LINES,
    max_items: int = MAX_ITEM_NAMES,
) -> str:
    """Describe the home's real categories, positions and item names.

    Slices *before* joining so a five-thousand-item home never builds a giant
    string only to throw it away.
    """
    categories = _categories(slots, items)
    lines: list[str] = [
        "【家中真实数据 — 以下名称只能原样引用，禁止编造】",
        "可选 category（物品大类，只能取下列值之一；无法对应时留空字符串）：",
    ]
    lines.append(", ".join(categories) if categories else "（暂无，任何情况下都留空字符串）")

    # Room names alone are valid hints too ("厨房里有什么？"), and the matcher
    # accepts them — without this line the model has no way to know that.
    rooms = _rooms(slots)
    if rooms:
        lines.append(f"房间（location_hint 可以直接填房间名）：{'、'.join(rooms)}")

    # Answers the structure questions directly: how many rooms / cabinets /
    # positions the home has, and whether there is room left.
    lines.append(_structure_summary(slots))

    lines.append("可选 location_hint（更具体的位置名称，只能引用下列之一）：")
    shown = slots[:max_slots]
    if shown:
        lines.extend(f"- {slot.get('full_path') or ''}" for slot in shown)
    else:
        lines.append("（暂无收纳位置，任何情况下都留空字符串）")
    if len(slots) > len(shown):
        lines.append(f"（共 {len(slots)} 个位置，此处仅列出前 {len(shown)} 个）")

    names = [str(i.get("name")) for i in items[:max_items] if i.get("name")]
    if names:
        lines.append(f"家中已有物品示例（最多 {len(names)} 个）：{'、'.join(names)}")
    else:
        lines.append("家中目前还没有任何物品。")

    return "\n".join(lines)


__all__ = ["MAX_ITEM_NAMES", "MAX_SLOT_LINES", "build_home_context"]
