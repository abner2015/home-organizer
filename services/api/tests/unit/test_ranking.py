"""Unit tests for the deterministic ranker (docs/AGENT.md §4.2 / §4.4).

The ranker used to be flat: ``_room_match`` read an ``item["usage_scene"]``
field no item row carries, so for a home whose slots all have
``allowed_categories=None`` and ``active_count=0`` every candidate scored the
same 40 and the LLM got no ordering signal. These tests pin the
``category → room_type`` table that gives rooms a voice, plus the
deterministic tie-break §4.4 demands.
"""
from __future__ import annotations

from typing import Any

from app.agents.ranking import _CATEGORY_ROOM_TYPES, deterministic_score, rank_slots


def _slot(
    *,
    room_type: str,
    path: str,
    slot_id: str | None = None,
    allowed_categories: list[str] | None = None,
    active_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": slot_id or path,
        "room_type": room_type,
        "full_path": path,
        "allowed_categories": allowed_categories,
        "active_count": active_count,
    }


def _item(category: str, name: str = "air fryer") -> dict[str, Any]:
    return {"name": name, "category": category}


def _score(slot: dict[str, Any], item: dict[str, Any]) -> int:
    return deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[]
    )


# ----------------------------------------------------------- room match table


def test_slot_in_a_matching_room_scores_higher_than_one_in_another_room() -> None:
    item = _item("utensil")
    kitchen = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1")
    living = _slot(room_type="living", path="客厅/装饰柜/第1层/S-1")
    assert _score(kitchen, item) > _score(living, item)


def test_bedroom_categories_match_bedroom_rooms() -> None:
    item = _item("clothes")
    bedroom = _slot(room_type="bedroom", path="主卧/衣柜/大衣区/S-1")
    kitchen = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1")
    assert _score(bedroom, item) > _score(kitchen, item)


def test_every_category_supports_at_least_one_room_type() -> None:
    """A category with an empty room set is as useless as a missing row."""
    for category, rooms in _CATEGORY_ROOM_TYPES.items():
        assert rooms, f"{category} maps to no room type"


def test_unknown_category_gets_no_room_signal() -> None:
    item = _item("unknown")
    kitchen = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1")
    living = _slot(room_type="living", path="客厅/装饰柜/第1层/S-1")
    assert _score(kitchen, item) == _score(living, item)


def test_missing_room_type_scores_zero_room_match() -> None:
    item = _item("utensil")
    slot = _slot(room_type="", path="?/?/?/S-1")
    known = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1")
    assert _score(slot, item) < _score(known, item)


# ------------------------------------------------------------------ ordering


def test_rank_slots_puts_the_matching_room_first() -> None:
    item = _item("medicine")
    slots = [
        _slot(room_type="living", path="客厅/装饰柜/第1层/S-1"),
        _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1"),
        _slot(room_type="bedroom", path="主卧/衣柜/带锁抽屉/S-1"),
    ]
    ranked = rank_slots(slots, item=item, preferences=[], history=[], soft_rules=[])
    assert ranked[0]["room_type"] == "bedroom"


def test_rank_slots_is_deterministic_under_score_ties() -> None:
    """Ties must not depend on input order (the DB's row order is unstable)."""
    item = _item("clothes")
    slots = [
        _slot(room_type="bedroom", path="儿童房/收纳柜/绘本架/S-2"),
        _slot(room_type="bedroom", path="主卧/衣柜/大衣区/S-1"),
    ]
    forward = [s["full_path"] for s in rank_slots(
        slots, item=item, preferences=[], history=[], soft_rules=[]
    )]
    backward = [s["full_path"] for s in rank_slots(
        list(reversed(slots)), item=item, preferences=[], history=[], soft_rules=[]
    )]
    assert forward == backward
    # full_path ascending is the tie-break, regardless of which room's UUID is
    # smaller — that is what makes runs reproducible.
    assert forward == ["主卧/衣柜/大衣区/S-1", "儿童房/收纳柜/绘本架/S-2"]


def test_rank_slots_attaches_the_score_to_every_row() -> None:
    item = _item("food")
    slots = [
        _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1"),
        _slot(room_type="living", path="客厅/装饰柜/第1层/S-1"),
    ]
    ranked = rank_slots(slots, item=item, preferences=[], history=[], soft_rules=[])
    assert [r["det_score"] for r in ranked] == sorted(
        (r["det_score"] for r in ranked), reverse=True
    )
    assert all("det_score" in r for r in ranked)


def test_rank_slots_discriminates_where_it_used_to_be_flat() -> None:
    """Regression: the old ranker tied every slot at 40 for this exact shape."""
    item = _item("utensil")
    slots = [
        _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1"),
        _slot(room_type="living", path="客厅/装饰柜/第1层/S-1"),
        _slot(room_type="bathroom", path="卫生间/吊柜/洗漱区/S-1"),
        _slot(room_type="bedroom", path="主卧/衣柜/抽屉/S-1"),
    ]
    ranked = rank_slots(slots, item=item, preferences=[], history=[], soft_rules=[])
    assert ranked[0]["full_path"] == "厨房/吊柜/第1层/S-1"
    assert len({r["det_score"] for r in ranked}) > 1
