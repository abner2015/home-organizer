"""Unit tests for the deterministic ranker (docs/AGENT.md §4.2 / §4.4).

The ranker used to be flat: ``_room_match`` read an ``item["usage_scene"]``
field no item row carries, so for a home whose slots all have
``allowed_categories=None`` and ``active_count=0`` every candidate scored the
same 40 and the LLM got no ordering signal. These tests pin the
``category → room_type`` table that gives rooms a voice, plus the
deterministic tie-break §4.4 demands.
"""
from __future__ import annotations

import uuid
from typing import Any

from app.agents.ranking import (
    _CATEGORY_ROOM_TYPES,
    _WEIGHTS,
    deterministic_score,
    rank_slots,
    score_terms,
)


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


# ------------------------------------------------------------- preference (P0.4)


def _pref(slot_id: str, category: str, count: int = 1) -> dict[str, Any]:
    return {"value": {"slots": {slot_id: {"category": category, "count": count}}}}


def test_preference_boost_is_exactly_ten() -> None:
    item = _item("utensil")
    liked = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    other = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-2", slot_id="b")
    prefs = [_pref("a", "utensil")]
    liked_score = deterministic_score(
        liked, item=item, preferences=prefs, history=[], soft_rules=[]
    )
    other_score = deterministic_score(
        other, item=item, preferences=prefs, history=[], soft_rules=[]
    )
    assert liked_score - other_score == 10


def test_preference_does_not_boost_a_different_category() -> None:
    """Accepting a mug's slot must not push medicine into that same drawer."""
    item = _item("medicine")
    slot = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    boosted = deterministic_score(
        slot, item=item, preferences=[_pref("a", "utensil")], history=[], soft_rules=[]
    )
    baseline = deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[]
    )
    assert boosted == baseline


def test_preference_with_no_stored_category_matches_any_item() -> None:
    item = _item("medicine")
    slot = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    boosted = deterministic_score(
        slot, item=item, preferences=[_pref("a", "")], history=[], soft_rules=[]
    )
    baseline = deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[]
    )
    assert boosted - baseline == 10


def test_legacy_preferred_slot_ids_shape_is_still_honoured() -> None:
    item = _item("utensil")
    slot = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    legacy = [{"value": {"preferred_slot_ids": ["a"]}}]
    assert deterministic_score(
        slot, item=item, preferences=legacy, history=[], soft_rules=[]
    ) - deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[]
    ) == 10


def test_score_equals_the_weighted_sum_of_its_terms() -> None:
    """The reason builder reads ``score_terms``; the sum must reproduce the
    score exactly or the explanation would not match the ranking."""
    item = _item("utensil")
    slot = _slot(
        room_type="kitchen",
        path="厨房/吊柜/第1层/S-1",
        allowed_categories=["utensil"],
    )
    prefs = [_pref("厨房/吊柜/第1层/S-1", "utensil")]
    history = [{"slot_id": "厨房/吊柜/第1层/S-1"}]
    terms = score_terms(
        slot, item=item, preferences=prefs, history=history, soft_rules=[]
    )
    assert deterministic_score(
        slot, item=item, preferences=prefs, history=history, soft_rules=[]
    ) == sum(_WEIGHTS[k] * v for k, v in terms.items())


def test_every_ranked_row_carries_a_chinese_reason() -> None:
    item = _item("food", name="空气炸锅")
    slots = [
        _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1"),
        _slot(room_type="living", path="客厅/装饰柜/第1层/S-1"),
    ]
    ranked = rank_slots(slots, item=item, preferences=[], history=[], soft_rules=[])
    for row in ranked:
        assert row["reason"]
        assert "空气炸锅" in row["reason"]
        assert not any("A" <= ch <= "z" for ch in row["reason"])


# ----------------------------------------------- cross-user preferences (P1.1)


def _owned_pref(
    slot_id: str,
    category: str,
    owner_id: uuid.UUID,
    *,
    is_personal: bool | None = None,
) -> dict[str, Any]:
    """A preference dict that carries the owner + an optional is_personal flag.

    Pass ``is_personal=None`` (default) to simulate a legacy entry that has no
    ``is_personal`` key at all; the ranker must treat that as shared.
    """
    entry: dict[str, Any] = {"category": category, "count": 1}
    if is_personal is not None:
        entry["is_personal"] = is_personal
    return {
        "user_id": str(owner_id),
        "value": {"slots": {slot_id: entry}},
    }


def test_non_sensitive_pref_from_another_user_boosts_the_actor() -> None:
    """P1.1: roommate's accept for a non-sensitive item is shared."""
    item = _item("utensil")
    slot = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    roommate = uuid.uuid4()
    me = uuid.uuid4()
    pref = _owned_pref("a", "utensil", roommate, is_personal=False)
    boosted = deterministic_score(
        slot,
        item=item,
        preferences=[pref],
        history=[],
        soft_rules=[],
        actor_user_id=me,
    )
    baseline = deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[],
        actor_user_id=me,
    )
    assert boosted - baseline == 10


def test_personal_pref_is_hidden_from_a_different_user() -> None:
    """P1.1: sensitive (is_personal=True) pref only influences the owner."""
    item = _item("medicine")
    slot = _slot(room_type="bedroom", path="主卧/床头柜/抽屉/S-1", slot_id="a")
    owner = uuid.uuid4()
    intruder = uuid.uuid4()
    pref = _owned_pref("a", "medicine", owner, is_personal=True)
    intruder_view = deterministic_score(
        slot,
        item=item,
        preferences=[pref],
        history=[],
        soft_rules=[],
        actor_user_id=intruder,
    )
    baseline = deterministic_score(
        slot,
        item=item,
        preferences=[],
        history=[],
        soft_rules=[],
        actor_user_id=intruder,
    )
    assert intruder_view == baseline


def test_personal_pref_remains_visible_to_its_owner() -> None:
    """P1.1: the actor must still see their own sensitive pref (+10)."""
    item = _item("medicine")
    slot = _slot(room_type="bedroom", path="主卧/床头柜/抽屉/S-1", slot_id="a")
    me = uuid.uuid4()
    pref = _owned_pref("a", "medicine", me, is_personal=True)
    own_score = deterministic_score(
        slot,
        item=item,
        preferences=[pref],
        history=[],
        soft_rules=[],
        actor_user_id=me,
    )
    baseline = deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[],
        actor_user_id=me,
    )
    assert own_score - baseline == 10


def test_legacy_pref_without_is_personal_is_treated_as_shared() -> None:
    """P1.1: entries written before this field was added must still boost."""
    item = _item("utensil")
    slot = _slot(room_type="kitchen", path="厨房/吊柜/第1层/S-1", slot_id="a")
    roommate = uuid.uuid4()
    me = uuid.uuid4()
    pref = _owned_pref("a", "utensil", roommate, is_personal=None)
    boosted = deterministic_score(
        slot,
        item=item,
        preferences=[pref],
        history=[],
        soft_rules=[],
        actor_user_id=me,
    )
    baseline = deterministic_score(
        slot, item=item, preferences=[], history=[], soft_rules=[],
        actor_user_id=me,
    )
    assert boosted - baseline == 10
