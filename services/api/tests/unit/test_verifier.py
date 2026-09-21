"""Unit tests for the Verifier (9 checks + run_verifier)."""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.verification.checks import (
    check_capacity,
    check_hard_safety,
    check_home_rules,
    check_no_hallucinated_location,
    check_no_more_obvious_conflict,
    check_reason_consistent,
    check_slot_belongs_to_home,
    check_slot_exists,
    check_user_preferences,
)
from app.verification.context import VerificationContext
from app.verification.verifier import run_verifier

pytestmark = pytest.mark.asyncio


# ----------------------------------------------------------- helpers


def _slot_dict(
    sid: uuid.UUID,
    *,
    code: str = "L1",
    home_id: uuid.UUID | None = None,
    room_type: str = "living",
    unit_type: str = "cabinet",
    capacity_hint: str | None = None,
    full_path: str | None = None,
    section_name: str = "左玻璃柜",
    section_type: str = "layer",
    label: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "id": str(sid),
        "section_id": str(uuid.uuid4()),
        "code": code,
        "label": label if label is not None else code,
        "capacity_hint": capacity_hint,
        "allowed_categories": [],
        "sort_order": 0,
        "home_id": str(home_id) if home_id else None,
        "room_id": str(uuid.uuid4()),
        "room_name": "客厅",
        "room_type": room_type,
        "unit_id": str(uuid.uuid4()),
        "unit_name": "装饰柜",
        "unit_type": unit_type,
        "section_name": section_name,
        "section_type": section_type,
        "full_path": full_path or f"客厅/装饰柜/左玻璃/{code}",
        "active_count": 0,
        "reason": reason,
    }


def _ctx_for(slot: dict[str, Any], **overrides: Any) -> VerificationContext:
    """Build a VerificationContext in which ``slot`` is the only known slot."""
    sid = uuid.UUID(str(slot["id"]))
    home_id = overrides.get("home_id", uuid.UUID(str(slot["home_id"])))
    base = VerificationContext(
        home_id=home_id,
        known_slot_ids=frozenset({sid}),
        whitelist_slot_ids=frozenset({sid}),
        slots_by_id={sid: slot},
        active_count=overrides.get("active_count", {sid: slot.get("active_count", 0)}),
        item=overrides.get(
            "item",
            {
                "name": "马克杯",
                "category": "utensil",
                "subcategory": "cup",
                "is_sensitive": False,
                "needs_lock": False,
                "estimated_size": "small",
            },
        ),
        hard_rules=overrides.get("hard_rules", ()),
        user_preferences=overrides.get("user_preferences", ()),
        history_items=overrides.get("history_items", ()),
    )
    return base


def _food_kitchen_rule() -> dict[str, Any]:
    """The seed's 厨房不放过期食品 rule: food must be placed in the kitchen."""
    return {
        "id": str(uuid.uuid4()),
        "name": "厨房不放过期食品",
        "description": "生鲜、调味料等有时效性的食品不允许长期存放在厨房以外的柜子中。",
        "rule_type": "hard",
        "scope": {"room_types": ["kitchen"], "categories": ["food"]},
        "enabled": True,
    }


# ----------------------------------------------------------- check 1


async def test_check_slot_exists_pass() -> None:
    slot = _slot_dict(uuid.uuid4(), home_id=uuid.uuid4())
    ctx = _ctx_for(slot)
    result = check_slot_exists(ctx, slot)
    assert result.passed is True
    assert result.code == "slot_exists"


async def test_check_slot_exists_fail() -> None:
    slot = _slot_dict(uuid.uuid4(), home_id=uuid.uuid4())
    ctx = _ctx_for(slot)
    fake = _slot_dict(uuid.uuid4(), home_id=ctx.home_id)
    result = check_slot_exists(ctx, fake)
    assert result.passed is False
    assert "不在候选集合中" in result.message


# ----------------------------------------------------------- check 2


async def test_check_slot_belongs_to_home_pass() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(slot, home_id=home_id)
    assert check_slot_belongs_to_home(ctx, slot).passed is True


async def test_check_slot_belongs_to_home_fail() -> None:
    slot = _slot_dict(uuid.uuid4(), home_id=uuid.uuid4())
    ctx = _ctx_for(slot, home_id=uuid.uuid4())
    assert check_slot_belongs_to_home(ctx, slot).passed is False


# ----------------------------------------------------------- check 3


async def test_check_capacity_pass() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, capacity_hint="medium")
    ctx = _ctx_for(slot, home_id=home_id, active_count={uuid.UUID(slot["id"]): 2})
    assert check_capacity(ctx, slot).passed is True


async def test_check_capacity_fail() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, capacity_hint="small")
    ctx = _ctx_for(slot, home_id=home_id, active_count={uuid.UUID(slot["id"]): 1})
    result = check_capacity(ctx, slot)
    assert result.passed is False
    assert "已放 1 件，容量上限 1 件" in result.message


async def test_check_capacity_infinite_when_no_hint() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, capacity_hint=None)
    ctx = _ctx_for(slot, home_id=home_id, active_count={uuid.UUID(slot["id"]): 999})
    assert check_capacity(ctx, slot).passed is True


# ----------------------------------------------------------- check 4


async def test_check_hard_safety_pass_non_sensitive() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, unit_type="shelf")
    ctx = _ctx_for(slot, home_id=home_id)
    assert check_hard_safety(ctx, slot).passed is True


async def test_check_hard_safety_fail_sensitive_in_cabinet() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, unit_type="cabinet")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={
            "name": "处方药",
            "category": "medicine",
            "is_sensitive": True,
            "needs_lock": True,
        },
    )
    result = check_hard_safety(ctx, slot)
    assert result.passed is False
    assert "带锁" in result.message


async def test_check_hard_safety_pass_sensitive_in_drawer() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, unit_type="drawer_cabinet")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "处方药", "is_sensitive": True, "needs_lock": True},
    )
    assert check_hard_safety(ctx, slot).passed is True


async def test_check_hard_safety_pass_locked_section_in_plain_cabinet() -> None:
    """A locked drawer inside a plain cabinet is a valid target.

    Regression: the kids' room stores medicine in 药品带锁抽屉, which hangs
    off a CABINET unit. Keying only on unit_type made that slot — and the
    golden case expecting it — permanently unreachable.
    """
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        unit_type="cabinet",
        section_name="药品带锁抽屉",
        section_type="drawer",
    )
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "儿童退烧药", "is_sensitive": True, "needs_lock": True},
    )
    assert check_hard_safety(ctx, slot).passed is True


async def test_check_hard_safety_pass_locked_english_label() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        unit_type="cabinet",
        section_name="Shelf",
        section_type="drawer",
        label="Locked drawer",
    )
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "处方药", "is_sensitive": True, "needs_lock": True},
    )
    assert check_hard_safety(ctx, slot).passed is True


async def test_check_hard_safety_fail_sensitive_in_unlocked_drawer() -> None:
    """An ordinary drawer (no lock marker) must still be rejected."""
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        unit_type="cabinet",
        section_name="玩具抽屉",
        section_type="drawer",
    )
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "处方药", "is_sensitive": True, "needs_lock": True},
    )
    assert check_hard_safety(ctx, slot).passed is False


# ----------------------------------------------------------- check 5


async def test_check_home_rules_pass_no_rules() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(slot, home_id=home_id, hard_rules=())
    assert check_home_rules(ctx, slot).passed is True


async def test_check_home_rules_pass_food_in_the_kitchen() -> None:
    """The seed rule's prose contains 不/不允许 but it *requires* the kitchen."""
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="kitchen")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={
            "name": "酸奶",
            "category": "food",
            "subcategory": "dairy",
            "is_sensitive": False,
        },
        hard_rules=(_food_kitchen_rule(),),
    )
    assert check_home_rules(ctx, slot).passed is True


async def test_check_home_rules_fail_food_outside_the_kitchen() -> None:
    """The same rule breaches a bedroom placement — that is what it forbids."""
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="bedroom")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "酸奶", "category": "food", "is_sensitive": False},
        hard_rules=(_food_kitchen_rule(),),
    )
    result = check_home_rules(ctx, slot)
    assert result.passed is False
    assert "厨房不放过期食品" in result.message


async def test_check_home_rules_pass_unrelated_scope() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="bedroom")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "杯子", "category": "utensil"},
        hard_rules=(_food_kitchen_rule(),),
    )
    # bedroom ≠ kitchen → rule scope doesn't apply.
    assert check_home_rules(ctx, slot).passed is True


# ----------------------------------------------------------- check 6


async def test_check_user_preferences_pass_no_avoid() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        user_preferences=({"value": {"theme": "dark"}},),
    )
    assert check_user_preferences(ctx, slot).passed is True


async def test_check_user_preferences_fail_in_avoid_list() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        user_preferences=({"value": {"avoid_slot_ids": [slot["id"]]}},),
    )
    result = check_user_preferences(ctx, slot)
    assert result.passed is False
    assert "不常用" in result.message


# ----------------------------------------------------------- check 7


async def test_check_reason_consistent_pass_token_overlap() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, reason="马克杯是常用餐具，放这里方便取用")
    ctx = _ctx_for(slot, home_id=home_id)
    assert check_reason_consistent(ctx, slot).passed is True


async def test_check_reason_consistent_pass_full_path_mention() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        reason="餐厅旁边的装饰柜",
        full_path="客厅/装饰柜/左玻璃/L1",
    )
    # item name has no token overlap but full_path has "装饰柜" mentioned
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "unknown", "category": "misc"},
    )
    assert check_reason_consistent(ctx, slot).passed is True


async def test_check_reason_consistent_fail() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, reason="存放在合适的地方")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "马克杯", "category": "utensil", "subcategory": "cup"},
    )
    result = check_reason_consistent(ctx, slot)
    assert result.passed is False


async def test_check_reason_consistent_fail_empty() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, reason="")
    ctx = _ctx_for(slot, home_id=home_id)
    result = check_reason_consistent(ctx, slot)
    assert result.passed is False
    assert "推荐理由为空" in result.message


# ----------------------------------------------------------- check 8


async def test_check_no_more_obvious_conflict_pass() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="living")
    ctx = _ctx_for(slot, home_id=home_id)
    assert check_no_more_obvious_conflict(ctx, slot).passed is True


async def test_check_no_more_obvious_conflict_fail() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="bedroom")
    ctx = _ctx_for(
        slot, home_id=home_id, item={"name": "酸奶", "category": "food"}
    )
    result = check_no_more_obvious_conflict(ctx, slot)
    assert result.passed is False
    assert "卧室" in result.message


async def test_check_no_more_obvious_conflict_pass_medicine_in_bedroom() -> None:
    """Medicine in a bedroom is legitimate (docs/AGENT.md §4.2).

    Regression: a blanket bedroom ban made every sensitive-medicine golden
    case unsatisfiable, because the locked drawer lives in the master bedroom.
    """
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id, room_type="bedroom")
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        item={"name": "处方药", "category": "medicine", "is_sensitive": True},
    )
    assert check_no_more_obvious_conflict(ctx, slot).passed is True


# ----------------------------------------------------------- check 9


async def test_check_no_hallucinated_location_pass() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(slot, home_id=home_id)
    assert check_no_hallucinated_location(ctx, slot).passed is True


async def test_check_no_hallucinated_location_fail() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(uuid.uuid4(), home_id=home_id)
    ctx = _ctx_for(slot, home_id=home_id)
    hallucinated = _slot_dict(uuid.uuid4(), home_id=home_id)
    result = check_no_hallucinated_location(ctx, hallucinated)
    assert result.passed is False
    assert "白名单" in result.message


# ----------------------------------------------------------- run_verifier


async def test_run_verifier_all_pass() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        room_type="living",
        capacity_hint="medium",
        reason="马克杯放在客厅装饰柜方便取用",
    )
    ctx = _ctx_for(slot, home_id=home_id, active_count={uuid.UUID(slot["id"]): 0})
    result = run_verifier(ctx, slot)
    assert result.ok is True
    assert result.failed == ()


async def test_run_verifier_first_failure_is_first_check() -> None:
    """If multiple checks fail, run_verifier returns all in declaration order."""
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=uuid.uuid4(),  # wrong home
        room_type="bedroom",
        capacity_hint="small",
        reason="无关描述",
    )
    # Make the slot id NOT in the known set so check 1 also fails.
    ctx = VerificationContext(
        home_id=home_id,
        known_slot_ids=frozenset(),
        whitelist_slot_ids=frozenset(),
        slots_by_id={},
        active_count={},
        item={"name": "酸奶", "category": "food", "is_sensitive": False},
        hard_rules=(),
        user_preferences=(),
    )
    result = run_verifier(ctx, slot)
    assert result.ok is False
    codes = [c.code for c in result.failed]
    assert codes[0] == "slot_exists"
    assert "slot_belongs_to_home" in codes


async def test_run_verifier_reason_only_failure() -> None:
    home_id = uuid.uuid4()
    slot = _slot_dict(
        uuid.uuid4(),
        home_id=home_id,
        room_type="living",
        capacity_hint="medium",
        reason="无关描述",
    )
    ctx = _ctx_for(
        slot,
        home_id=home_id,
        active_count={uuid.UUID(slot["id"]): 0},
        item={"name": "马克杯", "category": "utensil"},
    )
    result = run_verifier(ctx, slot)
    assert result.ok is False
    assert len(result.failed) == 1
    assert result.failed[0].code == "reason_consistent"
    # First-failure message is what the orchestrator feeds back to the LLM.
    assert result.message != ""
