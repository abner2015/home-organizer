"""Recommendation agent — 9 in-process scenarios. Scenario 10 (PATCH then accept)
lives in ``tests/api/test_recommendation_api.py`` because it needs the API +
placement_service wired together.

Each scenario uses:
- the real SQLite DB (via ``db_engine`` + ``storage_hierarchy``),
- a ``ToolRegistry`` built from real tool callables,
- a ``MockAIProvider`` with ``ranking_responses=[...]`` for retry tests.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents import RecommendationAgent
from app.agents.state import RecommendationState
from app.ai.providers.mock import MockAIProvider
from app.db.enums import (
    HomeRuleType,
    StorageSectionType,
    StorageUnitType,
)
from app.models import (
    HomeRule,
    Item,
    ItemPlacement,
    StorageSection,
    StorageSlot,
    StorageUnit,
)
from app.models.preference import UserPreference
from app.models.room import Room
from app.tools import get_default_registry

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- helpers


def _candidate_payload(slot_id: str, *, confidence: float = 0.9, reason: str = "") -> dict:
    return {
        "candidates": [
            {
                "slot_id": slot_id,
                "confidence": confidence,
                "reason": reason,
                "matched_rules": [],
                "evidence_item_ids": [],
            }
        ]
    }


class _SessionHolder:
    """Async-context-manager wrapper around a freshly-opened AsyncSession."""

    def __init__(self, engine) -> None:
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        self._sess = self._factory()
        return self._sess

    async def __aexit__(self, *exc: object) -> None:
        await self._sess.close()


async def _build_agent(
    db_engine, mock: MockAIProvider, *, max_retries: int = 2
) -> tuple[RecommendationAgent, _SessionHolder]:
    holder = _SessionHolder(db_engine)
    sess = await holder.__aenter__()
    reg = get_default_registry(sess)
    agent = RecommendationAgent(ai=mock, tools=reg, db=sess, max_retries=max_retries)
    return agent, holder


async def _seed_hard_rule(db_engine, home_id: uuid.UUID) -> None:
    async with _SessionHolder(db_engine) as session:
        session.add(
            HomeRule(
                home_id=home_id,
                name="厨房不放过期食品",
                description="生鲜、调味料等有时效性的食品不允许长期存放在厨房以外的柜子中。",
                rule_type=HomeRuleType.HARD.value,
                scope={"room_types": ["kitchen"], "categories": ["food"]},
                enabled=True,
            )
        )
        await session.commit()


async def _wipe_kitchen(db_engine, hierarchy) -> None:
    """Delete every slot/section/unit/room under the kitchen room."""
    async with _SessionHolder(db_engine) as session:
        section_ids = (
            await session.execute(
                select(StorageSection.id)
                .join(StorageUnit, StorageUnit.id == StorageSection.unit_id)
                .where(StorageUnit.room_id == hierarchy.kitchen_room_id)
            )
        ).scalars().all()
        if section_ids:
            await session.execute(
                delete(StorageSlot).where(StorageSlot.section_id.in_(section_ids))
            )
            await session.execute(
                delete(StorageSection).where(StorageSection.id.in_(section_ids))
            )
        await session.execute(
            delete(StorageUnit).where(StorageUnit.room_id == hierarchy.kitchen_room_id)
        )
        await session.execute(delete(Room).where(Room.id == hierarchy.kitchen_room_id))
        await session.commit()


async def _add_medicine_allowed_to_kitchen(db_engine, hierarchy) -> str:
    """Add a new drawer_cabinet in the kitchen whose slot accepts medicine."""
    async with _SessionHolder(db_engine) as session:
        unit = StorageUnit(
            room_id=hierarchy.kitchen_room_id,
            name="厨房药箱",
            unit_type=StorageUnitType.DRAWER_CABINET.value,
            sort_order=99,
        )
        session.add(unit)
        await session.flush()
        section = StorageSection(
            unit_id=unit.id,
            name="抽屉",
            section_type=StorageSectionType.DRAWER.value,
            sort_order=1,
        )
        session.add(section)
        await session.flush()
        slot = StorageSlot(
            section_id=section.id,
            code="K1",
            label="厨房药箱抽屉",
            allowed_categories=["medicine", "misc"],
            sort_order=1,
        )
        session.add(slot)
        await session.commit()
        return str(slot.id)


async def _wipe_bedside(db_engine, hierarchy) -> None:
    """Delete the bedside table hierarchy so only cabinet-type units remain."""
    async with _SessionHolder(db_engine) as session:
        section_ids = (
            await session.execute(
                select(StorageSection.id).where(StorageSection.unit_id == hierarchy.bedside_id)
            )
        ).scalars().all()
        if section_ids:
            await session.execute(
                delete(StorageSlot).where(StorageSlot.section_id.in_(section_ids))
            )
            await session.execute(
                delete(StorageSection).where(StorageSection.id.in_(section_ids))
            )
        await session.execute(
            delete(StorageUnit).where(StorageUnit.id == hierarchy.bedside_id)
        )
        await session.commit()


async def _wipe_all_slots(db_engine) -> None:
    async with _SessionHolder(db_engine) as session:
        await session.execute(delete(StorageSlot))
        await session.commit()


async def _seed_one_full_kitchen_utensil_slot(
    db_engine, hierarchy, *, filler_user_id
) -> str:
    """Add a single 'small' capacity kitchen utensil slot, already full.

    Used to test capacity-check retries: the cup can reach the slot but
    the verifier rejects every retry because ``active_count == capacity``.
    """
    async with _SessionHolder(db_engine) as session:
        unit = StorageUnit(
            room_id=hierarchy.kitchen_room_id,
            name="临时吊柜",
            unit_type=StorageUnitType.CABINET.value,
            sort_order=99,
        )
        session.add(unit)
        await session.flush()
        section = StorageSection(
            unit_id=unit.id,
            name="第1层",
            section_type=StorageSectionType.LAYER.value,
            sort_order=1,
        )
        session.add(section)
        await session.flush()
        slot = StorageSlot(
            section_id=section.id,
            code="Z1",
            label="临时Z1",
            capacity_hint="small",
            allowed_categories=["utensil"],
            sort_order=1,
        )
        session.add(slot)
        # Flush so slot.id is populated before we reference it.
        await session.flush()
        # Fill the slot so capacity (1) == active_count.
        session.add(
            ItemPlacement(
                item_id=hierarchy.items["处方药"],
                slot_id=slot.id,
                placed_by=filler_user_id,
                source="user_manual",
            )
        )
        await session.commit()
        return str(slot.id)


async def _add_kitchen_cabinet_for_medicine(db_engine, hierarchy) -> str:
    """Add a regular cabinet (not drawer_cabinet/box) in the kitchen whose
    slot accepts medicine. Used by scenario 4 to give the medicine item
    exactly one candidate, which then fails check_hard_safety on every retry.
    """
    async with _SessionHolder(db_engine) as session:
        unit = StorageUnit(
            room_id=hierarchy.kitchen_room_id,
            name="厨房药品柜",
            unit_type=StorageUnitType.CABINET.value,  # not safe-for-sensitive
            sort_order=99,
        )
        session.add(unit)
        await session.flush()
        section = StorageSection(
            unit_id=unit.id,
            name="第1层",
            section_type=StorageSectionType.LAYER.value,
            sort_order=1,
        )
        session.add(section)
        await session.flush()
        slot = StorageSlot(
            section_id=section.id,
            code="K1",
            label="厨房药品柜K1",
            allowed_categories=["medicine"],
            sort_order=1,
        )
        session.add(slot)
        await session.commit()
        return str(slot.id)


# ============================================================== scenarios


async def test_scenario_1_happy_path(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Happy path: item has a matching candidate; LLM picks it on first try.

    The cup has ``category='utensil'``; the candidate set is therefore the
    kitchen utensil slots (L1S1 / L1S2). The LLM must pick from there.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_response=_candidate_payload(slot_id, reason="马克杯放在厨房吊柜")
    )
    agent, holder = await _build_agent(db_engine, mock)

    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )

    await holder.__aexit__()
    assert result.ok, result.error
    assert result.chosen_slot_id == storage_hierarchy.slots["L1S1"]
    assert result.retries_used == 0
    assert result.state == RecommendationState.ANSWER
    states = [s.state for s in result.steps]
    assert RecommendationState.DECIDE in states
    assert RecommendationState.VERIFY in states


async def test_scenario_2_no_storage_slot(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """No slots at all → FAILED at the filter step, no LLM call."""
    await _wipe_all_slots(db_engine)
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider()
    agent, holder = await _build_agent(db_engine, mock)

    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert result.state == RecommendationState.FAILED
    assert result.chosen_slot_id is None
    assert "无符合硬规则的位置" in (result.error or "")
    assert mock.call_count == 0


async def test_scenario_3_insufficient_capacity_fails_after_two_retries(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A 1-slot, capacity=small already-full home; the LLM must fail twice and give up."""
    await _wipe_all_slots(db_engine)
    full_slot_id = await _seed_one_full_kitchen_utensil_slot(
        db_engine, storage_hierarchy, filler_user_id=seeded_actor.user_id
    )

    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(full_slot_id, reason="马克杯放在这里"),
            _candidate_payload(full_slot_id, reason="马克杯放在这里"),
            _candidate_payload(full_slot_id, reason="马克杯放在这里"),
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)

    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert result.state == RecommendationState.FAILED
    assert result.retries_used == 2
    assert "容量上限" in (result.error or "")
    assert mock.call_count == 3  # initial + 2 retries


async def test_scenario_4_safety_conflict(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Sensitive item, only cabinet units → check_hard_safety rejects every retry."""
    # Remove the bedside drawer_cabinet and add a regular cabinet in the
    # kitchen that accepts medicine — this becomes the only candidate and
    # fails check_hard_safety on every retry.
    await _wipe_bedside(db_engine, storage_hierarchy)
    target_slot = await _add_kitchen_cabinet_for_medicine(db_engine, storage_hierarchy)

    med_id = storage_hierarchy.items["处方药"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(target_slot, reason="处方药放在柜子"),
            _candidate_payload(target_slot, reason="处方药放在柜子"),
            _candidate_payload(target_slot, reason="处方药放在柜子"),
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=med_id,
    )
    await holder.__aexit__()
    assert result.state == RecommendationState.FAILED
    assert result.retries_used == 2
    assert "带锁" in (result.error or "")
    assert mock.call_count == 3


async def test_scenario_5_hard_rule_conflict(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Hard rule forbids food in bedroom; remove kitchen so food has no slot → FAILED at filter."""
    await _seed_hard_rule(db_engine, seeded_actor.home_id)
    await _wipe_kitchen(db_engine, storage_hierarchy)

    async with _SessionHolder(db_engine) as session:
        session.add(
            Item(
                home_id=seeded_actor.home_id,
                name="酸奶",
                category="food",
                subcategory="dairy",
                estimated_size="small",
                is_sensitive=False,
                needs_lock=False,
                created_by=seeded_actor.user_id,
            )
        )
        await session.commit()

    async with _SessionHolder(db_engine) as session:
        food = (
            await session.execute(
                select(Item).where(
                    Item.home_id == seeded_actor.home_id, Item.name == "酸奶"
                )
            )
        ).scalar_one()
        food_id = food.id

    mock = MockAIProvider()
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=food_id,
    )
    await holder.__aexit__()
    assert result.state == RecommendationState.FAILED
    assert "无符合硬规则的位置" in (result.error or "")
    assert mock.call_count == 0


async def test_scenario_6_user_preference_avoid_retry_succeeds(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """User marked slot L1S1 as 'avoid'; first attempt picks it → fail → retry → picks L1S2."""
    async with _SessionHolder(db_engine) as session:
        session.add(
            UserPreference(
                user_id=seeded_actor.user_id,
                home_id=seeded_actor.home_id,
                key="avoid_slots",
                # JSONBCompat requires JSON-serialisable values — coerce UUIDs.
                value={"avoid_slot_ids": [str(storage_hierarchy.slots["L1S1"])]},
            )
        )
        await session.commit()

    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(storage_hierarchy.slots["L1S1"], reason="马克杯放在这里"),
            _candidate_payload(storage_hierarchy.slots["L1S2"], reason="马克杯放在这里"),
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert result.ok, result.error
    assert result.chosen_slot_id == storage_hierarchy.slots["L1S2"]
    assert result.retries_used == 1


async def test_scenario_7_reason_inconsistent_retry_succeeds(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """First reason doesn't mention the item; second one does."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(slot_id, reason="随便放放"),  # no token overlap
            _candidate_payload(slot_id, reason="马克杯放在厨房吊柜"),  # overlap
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert result.ok, result.error
    assert result.retries_used == 1


async def test_scenario_8_retry_once_succeeds(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Two scripted responses; second one passes everything."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(slot_id, reason="不在理由范围内"),
            _candidate_payload(slot_id, reason="马克杯放在这里"),
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert result.ok
    assert result.retries_used == 1
    assert mock.call_count == 2


async def test_scenario_9_retry_twice_still_fails(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Three bad attempts (max_retries=2 → 1 initial + 2 retries = 3 calls)."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_responses=[
            _candidate_payload(slot_id, reason="a"),
            _candidate_payload(slot_id, reason="b"),
            _candidate_payload(slot_id, reason="c"),
        ]
    )
    agent, holder = await _build_agent(db_engine, mock, max_retries=2)
    result = await agent.run(
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        item_id=cup_id,
    )
    await holder.__aexit__()
    assert not result.ok
    assert result.state == RecommendationState.FAILED
    assert result.retries_used == 2
    assert mock.call_count == 3
    assert result.error


# Silence unused-import warning — Sequence is handy for future scenarios.
_ = Sequence
