"""Render the user's storage hierarchy into a Chinese blueprint.

Every other search intent is *item*-centric: they all end up asking
:func:`app.tools.item_tools.search_items` about ``Item`` rows. So when the user
asked 「我家有几个柜子？」 the model had nowhere to put the question — it filled
``query="柜子"`` and the agent dutifully searched for an *item* whose name
contains 柜子, then answered 「目前只查到1个柜子」. Similarly 「我家一共有几个房间？」
fell through to ``unknown`` and the assistant claimed it could not count rooms,
even though :func:`app.tools.home_tools.get_rooms` had the answer in one query.

This module is the missing half: a compact description of the home's *static
storage structure* — rooms, furniture, sections, positions and how full they
are. It is deliberately a plain text block rather than a typed answer, because
the same artifact has to serve 「有几个柜子」 (a count), 「客厅有哪些收纳」 (a list)
and 「收纳空间够不够用」 (capacity). The deterministic layer's job is to retrieve
the facts; :func:`app.agents.search.answer.compose_answer` lets the LLM phrase
whichever of those the user actually asked.

Pure function: no DB, no LLM. Stable output for a given input. The tool dicts
it consumes are the exact shapes returned by ``app.tools.home_tools``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

# A real home can have far more storage than fits in a prompt. The caps keep the
# block bounded; each truncation is announced rather than silently dropped.
MAX_ROOMS = 20
MAX_UNITS_PER_ROOM = 12
MAX_SECTIONS_PER_UNIT = 10
MAX_SLOTS_PER_SECTION = 6

# The vocabulary the user speaks. `unit_type` / `room_type` are ASCII enum values
# (`cabinet`, `bedroom`) — leaking those into the answer is exactly the English
# the recommendation UI used to show, so every label here is Chinese.
ROOM_TYPE_LABEL: dict[str, str] = {
    "bedroom": "卧室",
    "kitchen": "厨房",
    "bathroom": "卫生间",
    "study": "书房",
    "living": "客厅",
    "storage": "储物间",
    "other": "其他",
}

UNIT_TYPE_LABEL: dict[str, str] = {
    "cabinet": "柜子",
    "shelf": "架子",
    "drawer_cabinet": "抽屉柜",
    "box": "收纳箱",
    "other": "收纳家具",
}


@dataclass(slots=True)
class HomeBlueprint:
    """The rendered block plus what the caller needs to pick a state.

    ``text`` is empty when there is nothing to describe yet — a home with no
    rooms and no storage units. The caller turns that into ``not_found``.
    """

    text: str
    room_count: int
    unit_count: int
    slot_count: int
    placed_count: int


def _type_label(mapping: dict[str, str], value: Any) -> str:
    """Chinese label for an enum value, falling back to the raw value."""
    key = str(value or "").strip().lower()
    return mapping.get(key, key or "未分类")


def _room_label(room: dict[str, Any]) -> str:
    """``主卧（卧室）`` — but ``客厅`` alone, since the type word is already in it."""
    name = str(room.get("name") or "").strip() or "未命名房间"
    label = _type_label(ROOM_TYPE_LABEL, room.get("room_type"))
    return name if label in name else f"{name}（{label}）"


def _unit_label(unit: dict[str, Any]) -> str:
    name = str(unit.get("name") or "").strip() or "未命名收纳"
    return f"{name}（{_type_label(UNIT_TYPE_LABEL, unit.get('unit_type'))}）"


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    """Bucket ``rows`` by ``row[key]``, preserving first-seen order."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row.get(key) or ""), []).append(row)
    return out


def build_home_blueprint(
    *,
    rooms: list[dict[str, Any]],
    units: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    max_rooms: int = MAX_ROOMS,
    max_units_per_room: int = MAX_UNITS_PER_ROOM,
    max_sections_per_unit: int = MAX_SECTIONS_PER_UNIT,
    max_slots_per_section: int = MAX_SLOTS_PER_SECTION,
) -> HomeBlueprint:
    """Describe the home's rooms, furniture, positions and occupancy.

    Every dict comes from ``app.tools.home_tools``. Slicing happens *before*
    joining so a large home never builds a giant string only to discard it.
    """
    unit_count = len(units)
    slot_count = len(slots)
    # A slot's `active_count` is its number of live placements, so the sum is the
    # number of items currently put away — the numerator of the occupancy line.
    placed_count = sum(int(s.get("active_count") or 0) for s in slots)

    if not rooms and not units:
        return HomeBlueprint(
            text="",
            room_count=0,
            unit_count=0,
            slot_count=slot_count,
            placed_count=placed_count,
        )

    sections_by_unit = _group(sections, "unit_id")
    slots_by_section = _group(slots, "section_id")
    units_by_room = _group(units, "room_id")

    lines: list[str] = ["【家中收纳空间总览】"]

    shown_rooms = rooms[:max_rooms]
    room_names = "、".join(_room_label(r) for r in shown_rooms)
    if not room_names:
        # Units can exist without a matching Room row in theory; don't print a
        # bare "房间 0 个：" heading in that case.
        room_names = "（暂无房间记录）"
    suffix = "" if len(rooms) <= len(shown_rooms) else f"（共 {len(rooms)} 个，此处仅列前 {len(shown_rooms)} 个）"
    lines.append(f"房间 {len(rooms)} 个：{room_names}{suffix}")

    if units:
        by_type = Counter(
            _type_label(UNIT_TYPE_LABEL, u.get("unit_type")) for u in units
        )
        breakdown = "、".join(f"{label} {n} 件" for label, n in by_type.most_common())
        lines.append(f"收纳家具 {unit_count} 件：{breakdown}")
    else:
        lines.append("收纳家具 0 件：家里还没有添加柜子、架子之类的收纳空间。")

    free = max(slot_count - placed_count, 0)
    lines.append(
        f"收纳分区 {len(sections)} 处；收纳位 {slot_count} 个；"
        f"已放入物品 {placed_count} 件；空余 {free} 个。"
    )

    # Per-room detail. Rooms with no furniture are announced as empty rather
    # than skipped — "客厅没有收纳空间" is a real answer to 「客厅有哪些收纳」.
    for room in shown_rooms:
        room_units = units_by_room.get(str(room.get("id") or ""), [])
        lines.append("")
        lines.append(_room_label(room))
        if not room_units:
            lines.append("- （该房间还没有收纳家具）")
            continue
        for unit in room_units[:max_units_per_room]:
            lines.append(_unit_line(unit, sections_by_unit, slots_by_section,
                                    max_sections_per_unit, max_slots_per_section))
        if len(room_units) > max_units_per_room:
            lines.append(
                f"- （该房间共 {len(room_units)} 件收纳家具，此处仅列前 "
                f"{max_units_per_room} 件）"
            )

    if len(rooms) > len(shown_rooms):
        lines.append("")
        lines.append(
            f"（其余 {len(rooms) - len(shown_rooms)} 个房间未列出。）"
        )

    return HomeBlueprint(
        text="\n".join(lines),
        room_count=len(rooms),
        unit_count=unit_count,
        slot_count=slot_count,
        placed_count=placed_count,
    )


def _unit_line(
    unit: dict[str, Any],
    sections_by_unit: dict[str, list[dict[str, Any]]],
    slots_by_section: dict[str, list[dict[str, Any]]],
    max_sections: int,
    max_slots: int,
) -> str:
    """One furniture item + its sections, e.g.

    ``- 客厅装饰柜（柜子）：4 个分区、10 个收纳位 | 左玻璃柜（3 位：…）…``
    """
    unit_sections = sections_by_unit.get(str(unit.get("id") or ""), [])
    unit_slot_total = sum(
        len(slots_by_section.get(str(s.get("id") or ""), [])) for s in unit_sections
    )
    head = f"- {_unit_label(unit)}：{len(unit_sections)} 个分区、{unit_slot_total} 个收纳位"

    parts: list[str] = []
    for section in unit_sections[:max_sections]:
        section_name = str(section.get("name") or "未命名分区")
        section_slots = slots_by_section.get(str(section.get("id") or ""), [])
        # Slot `label` is already Chinese ("左玻璃柜第1层"); `code` is ASCII
        # machine identity and must never reach the user.
        names = [
            str(s.get("label") or s.get("code") or "").strip() for s in section_slots[:max_slots]
        ]
        names = [n for n in names if n]
        detail = "、".join(names)
        if len(section_slots) > len(names):
            detail = f"{detail} 等 {len(section_slots)} 个" if names else f"{len(section_slots)} 个"
        # A one-slot section is usually named after that slot ("大衣区" / "大衣区");
        # repeating it in brackets is noise.
        if not detail or detail == section_name:
            parts.append(section_name)
        else:
            parts.append(f"{section_name}（{detail}）")
    if len(unit_sections) > max_sections:
        parts.append(f"等共 {len(unit_sections)} 个分区")

    if parts:
        head += " | " + " ".join(parts)
    return head


__all__ = [
    "MAX_ROOMS",
    "ROOM_TYPE_LABEL",
    "UNIT_TYPE_LABEL",
    "HomeBlueprint",
    "build_home_blueprint",
]
