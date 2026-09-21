"""Pure tests for the prompt-grounding context block.

Regression origin: asked 「我家所有的厨房用品？」 the intent LLM emitted
``category="kitchen"`` — a *room type*, not a category — and ``search_items``
filters ``Item.category == category`` exactly, so the assistant answered
「家里没有「kitchen」的物品。」. The block below is what stops the model from
inventing vocabulary.
"""
from __future__ import annotations

from app.agents.search.context import build_home_context


def _slot(code: str, room: str, unit: str, section: str, allowed: list[str]) -> dict:
    # Mirrors the real `get_storage_slots` shape — `app.agents.search.context`
    # counts distinct units/sections from these ids.
    return {
        "id": f"id-{code}",
        "code": code,
        "label": f"{section}{code}",
        "room_name": room,
        "unit_id": f"unit-{unit}",
        "unit_name": unit,
        "unit_type": "cabinet",
        "section_id": f"section-{section}",
        "section_name": section,
        "full_path": f"{room}/{unit}/{section}/{code}",
        "allowed_categories": allowed,
        "active_count": 0,
    }


def test_categories_are_the_sorted_union_of_items_and_slots() -> None:
    slots = [_slot("L1", "客厅", "装饰柜", "玻璃柜", ["decor", "books"])]
    items = [{"name": "马克杯", "category": "utensil"}, {"name": "书", "category": "books"}]
    ctx = build_home_context(slots=slots, items=items)
    # decor (slot) + books (both) + utensil (item) — deduped and sorted.
    assert "books, decor, utensil" in ctx


def test_blank_and_missing_categories_are_ignored() -> None:
    slots = [_slot("L1", "客厅", "装饰柜", "玻璃柜", [])]
    items = [{"name": "无类别"}, {"name": "空类别", "category": ""}]
    ctx = build_home_context(slots=slots, items=items)
    assert "（暂无，任何情况下都留空字符串）" in ctx


def test_room_names_are_listed_so_a_bare_room_is_a_valid_hint() -> None:
    """Without this the model can't know 「厨房」 is a legal location_hint."""
    slots = [
        _slot("L1S1", "厨房", "厨房吊柜", "第1层", ["utensil"]),
        _slot("L1", "客厅", "客厅装饰柜", "左玻璃柜", ["decor"]),
    ]
    ctx = build_home_context(slots=slots, items=[])
    assert "房间（location_hint 可以直接填房间名）：厨房、客厅" in ctx
    # Order follows first appearance, and each room appears exactly once.
    assert ctx.count("厨房、客厅") == 1


def test_position_lines_use_full_path() -> None:
    slots = [_slot("L1", "客厅", "客厅装饰柜", "左玻璃柜", ["decor"])]
    ctx = build_home_context(slots=slots, items=[])
    assert "- 客厅/客厅装饰柜/左玻璃柜/L1" in ctx


def test_slots_are_truncated_with_the_true_total_named() -> None:
    slots = [
        _slot(f"S{i}", "客厅", "柜子", "层", ["misc"]) for i in range(10)
    ]
    ctx = build_home_context(slots=slots, items=[], max_slots=3)
    assert ctx.count("- 客厅/柜子/层/S") == 3
    assert "（共 10 个位置，此处仅列出前 3 个）" in ctx


def test_item_names_are_truncated() -> None:
    items = [{"name": f"物品{i}", "category": "misc"} for i in range(10)]
    ctx = build_home_context(slots=[], items=items, max_items=2)
    assert "物品0、物品1" in ctx
    assert "物品2" not in ctx


def test_structure_summary_makes_counts_visible_to_the_model() -> None:
    """Without this line 「我家有几个柜子？」 had no grounding at all."""
    slots = [
        _slot("L1", "客厅", "客厅装饰柜", "左玻璃柜", ["decor"]),
        _slot("L2", "客厅", "客厅装饰柜", "右玻璃柜", ["decor"]),
        _slot("D1", "主卧", "主卧衣柜", "抽屉", ["misc"]),
    ]
    ctx = build_home_context(slots=slots, items=[])
    assert "收纳结构：" in ctx
    assert "房间 2 个" in ctx
    assert "收纳家具 2 件" in ctx
    assert "柜子 2 件" in ctx  # 客厅装饰柜 + 主卧衣柜, both plain cabinets
    assert "抽屉柜" not in ctx
    assert "收纳分区 3 处" in ctx


def test_structure_summary_counts_units_by_their_own_type() -> None:
    """`unit_type` varies per unit — the mix must not be inferred from slots."""
    slots = [
        {**_slot("L1", "客厅", "装饰柜", "层", ["decor"])},
        {**_slot("D1", "主卧", "衣柜", "抽屉", ["misc"]), "unit_type": "drawer_cabinet"},
    ]
    ctx = build_home_context(slots=slots, items=[])
    assert "柜子 1 件" in ctx
    assert "抽屉柜 1 件" in ctx


def test_structure_summary_reports_occupancy() -> None:
    slots = [{**_slot("L1", "客厅", "柜", "层", ["decor"]), "active_count": 2}]
    ctx = build_home_context(slots=slots, items=[])
    assert "收纳位 1 个，其中 2 个已放物品" in ctx


def test_structure_summary_on_an_empty_home() -> None:
    ctx = build_home_context(slots=[], items=[])
    assert "收纳结构：家里还没有任何收纳位置。" in ctx


def test_empty_home_says_so_instead_of_listing_nothing() -> None:
    ctx = build_home_context(slots=[], items=[])
    assert "（暂无收纳位置，任何情况下都留空字符串）" in ctx
    assert "家中目前还没有任何物品。" in ctx


def test_deterministic_for_the_same_input() -> None:
    """The recorded ``prompt_hash`` must stay comparable across runs."""
    slots = [_slot("L1", "客厅", "装饰柜", "玻璃柜", ["decor"])]
    items = [{"name": "马克杯", "category": "utensil"}]
    assert build_home_context(slots=slots, items=items) == build_home_context(
        slots=slots, items=items
    )
