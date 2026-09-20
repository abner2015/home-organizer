"""Unit tests for the rule scope matcher (docs/DOMAIN.md §6, AGENT.md §5.2).

The regression this file exists for: ``violates`` used to decide polarity by
scanning the rule's prose for negation keywords, so a rule that *requires* the
kitchen was read as *forbidding* the kitchen and the hard filter dropped every
kitchen slot for food items.
"""
from __future__ import annotations

from typing import Any

from app.verification.rule_engine import applies, violates

KITCHEN_FOOD_RULE = {
    "name": "厨房不放过期食品",
    "description": "生鲜、调味料等有时效性的食品不允许长期存放在厨房以外的柜子中。",
    "scope": {"room_types": ["kitchen"], "categories": ["food"]},
}


def _slot(*, room_type: str = "kitchen", unit_type: str = "cabinet", room_id: str = "r1") -> dict[str, Any]:
    return {"room_type": room_type, "unit_type": unit_type, "room_id": room_id}


FOOD = {"name": "酸奶", "category": "food"}
CUP = {"name": "杯子", "category": "utensil"}


# --------------------------------------------------- scope = requirements


def test_rule_requires_its_room_and_permits_it() -> None:
    assert violates(_slot(room_type="kitchen"), KITCHEN_FOOD_RULE, FOOD) is False


def test_rule_is_breached_outside_its_room() -> None:
    assert violates(_slot(room_type="bedroom"), KITCHEN_FOOD_RULE, FOOD) is True


def test_prose_negation_does_not_invert_the_rule() -> None:
    """The rule says 不/不允许 but it is a positive placement requirement."""
    assert violates(_slot(room_type="kitchen"), KITCHEN_FOOD_RULE, FOOD) is False


def test_rule_does_not_cover_other_categories() -> None:
    assert violates(_slot(room_type="bedroom"), KITCHEN_FOOD_RULE, CUP) is False


def test_applies_is_false_for_an_uncovered_item() -> None:
    assert applies(KITCHEN_FOOD_RULE["scope"], CUP) is False
    assert applies(KITCHEN_FOOD_RULE["scope"], FOOD) is True


def test_empty_scope_covers_everything_but_requires_nothing() -> None:
    rule = {"name": "无范围规则", "scope": {}}
    assert applies({}, FOOD) is True
    assert violates(_slot(), rule, FOOD) is False


# --------------------------------------------------- other scope keys


def test_unit_type_requirement() -> None:
    rule = {"name": "r", "scope": {"slot_unit_types": ["drawer_cabinet"]}}
    assert violates(_slot(unit_type="cabinet"), rule, FOOD) is True
    assert violates(_slot(unit_type="drawer_cabinet"), rule, FOOD) is False


def test_room_id_requirement() -> None:
    rule = {"name": "r", "scope": {"room_id": "kitchen-1"}}
    assert violates(_slot(room_id="kitchen-2"), rule, FOOD) is True
    assert violates(_slot(room_id="kitchen-1"), rule, FOOD) is False


def test_requires_attributes() -> None:
    rule = {"name": "r", "scope": {"requires_attributes": ["needs_lock"]}}
    assert violates(_slot(), rule, {"category": "medicine", "needs_lock": False}) is True
    assert violates(_slot(), rule, {"category": "medicine", "needs_lock": True}) is False


def test_forbids_attributes() -> None:
    rule = {"name": "r", "scope": {"forbids_attributes": ["is_sensitive"]}}
    assert violates(_slot(), rule, {"category": "medicine", "is_sensitive": True}) is True
    assert violates(_slot(), rule, {"category": "utensil", "is_sensitive": False}) is False


def test_needs_lock_is_an_item_filter_not_a_requirement() -> None:
    """A rule scoped to lock-needing items doesn't breach lock-less items."""
    rule = {"name": "药品上锁", "scope": {"categories": ["medicine"], "needs_lock": True}}
    assert violates(
        _slot(room_type="living"), rule, {"category": "medicine", "needs_lock": True}
    ) is False
    assert violates(
        _slot(room_type="living"), rule, {"category": "medicine", "needs_lock": False}
    ) is False


def test_unknown_scope_keys_are_ignored() -> None:
    rule = {"name": "r", "scope": {"vibes": ["immaculate"]}}
    assert violates(_slot(), rule, FOOD) is False


def test_rule_without_scope_is_never_breached() -> None:
    assert violates(_slot(), {"name": "no scope"}, FOOD) is False
