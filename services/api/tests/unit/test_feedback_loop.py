"""The P0.4 feedback loop, end to end (accept → up, reject → gone).

This is the acceptance-criteria file for 「闭环」. Two directions:

- **Reject** writes nothing beyond ``status='rejected'``; the exclusion set is
  *derived* from those rows at RETRIEVE time and applied at FILTER time.
- **Accept** (and a manual placement) writes a category-scoped positive
  preference, which the ranker reads back on the next run.

Note on the accept test: it must use a **different item of the same category**,
never the same item. Accepting places the item, so a re-run of the *same* item
would gain both ``_history_match`` (+10) and ``_preference_match`` (+10) and
the two signals would be indistinguishable.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.placement_service import (
    accept_recommendation,
    place_item,
    reject_recommendation,
)
from app.ai.providers.mock import MockAIProvider
from app.models import Item, Recommendation
from app.models.preference import UserPreference
from app.services.recommendation_service import run_recommendation
from app.tools.recommendation_tools import get_rejected_slot_ids
from app.tools.write_tools import (
    PREFERENCE_KEY_PREFERRED_SLOTS,
    save_placement,
)

pytestmark = pytest.mark.asyncio


def _pick(slot_id: uuid.UUID, reason: str = "马克杯放在这里很合适") -> dict:
    return {
        "candidates": [
            {
                "slot_id": str(slot_id),
                "confidence": 0.9,
                "reason": reason,
                "matched_rules": [],
                "evidence_item_ids": [],
            }
        ]
    }


def _session(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)()


async def _make_item(session, actor, name: str, category: str) -> Item:
    item = Item(
        id=uuid.uuid4(),
        home_id=actor.home_id,
        name=name,
        category=category,
        estimated_size="small",
        is_sensitive=False,
        needs_lock=False,
        created_by=actor.user_id,
    )
    session.add(item)
    await session.commit()
    return item


def _by_id(ranked: list[dict], slot_id: uuid.UUID) -> tuple[int, int]:
    """(index, det_score) of a slot in the ranked candidate list."""
    for i, c in enumerate(ranked):
        if str(c["id"]) == str(slot_id):
            return i, int(c["det_score"])
    raise AssertionError(f"slot {slot_id} not in ranked candidates")


# --------------------------------------------------------------- reject → gone


async def test_rejected_slot_is_excluded_from_the_next_run(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    rejected = storage_hierarchy.slots["L1S1"]
    fallback = storage_hierarchy.slots["L1S2"]
    cup = storage_hierarchy.items["马克杯"]
    session = _session(db_engine)
    try:
        first = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(rejected)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        assert first.recommendation.chosen_slot_id == rejected

        await reject_recommendation(
            session,
            recommendation_id=first.recommendation.id,
            home_id=seeded_actor.home_id,
            note="位置太远",
        )
        assert await get_rejected_slot_ids(
            db=session, home_id=seeded_actor.home_id, item_id=cup
        ) == frozenset({rejected})

        second = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(fallback)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        assert second.ok
        candidate_ids = [str(c["id"]) for c in second.result.candidates]
        assert str(rejected) not in candidate_ids
        assert second.recommendation.chosen_slot_id != rejected
    finally:
        await session.close()


async def test_rejection_reason_is_recorded_on_the_audit_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup = storage_hierarchy.items["马克杯"]
    session = _session(db_engine)
    try:
        outcome = await run_recommendation(
            session,
            provider=MockAIProvider(
                ranking_responses=[_pick(storage_hierarchy.slots["L1S1"])]
            ),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        await reject_recommendation(
            session,
            recommendation_id=outcome.recommendation.id,
            home_id=seeded_actor.home_id,
            note="格子太小",
        )
        rec = (
            await session.execute(
                select(Recommendation).where(
                    Recommendation.id == outcome.recommendation.id
                )
            )
        ).scalar_one()
        assert rec.candidates[0]["audit_note"] == "格子太小"
        # chosen_slot_id deliberately survives rejection — it *is* the veto.
        assert rec.chosen_slot_id is not None
    finally:
        await session.close()


async def test_exclusion_is_scoped_to_the_item(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A veto is "not this slot for this item", not "not this slot at all"."""
    cup = storage_hierarchy.items["马克杯"]
    med = storage_hierarchy.items["处方药"]
    rejected = storage_hierarchy.slots["L1S1"]
    session = _session(db_engine)
    try:
        outcome = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(rejected)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        await reject_recommendation(
            session,
            recommendation_id=outcome.recommendation.id,
            home_id=seeded_actor.home_id,
            note="不合适",
        )
        assert await get_rejected_slot_ids(
            db=session, home_id=seeded_actor.home_id, item_id=cup
        ) == frozenset({rejected})
        assert await get_rejected_slot_ids(
            db=session, home_id=seeded_actor.home_id, item_id=med
        ) == frozenset()
    finally:
        await session.close()


async def test_only_rejected_rows_exclude_not_superseded(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Accepting a re-recommendation supersedes the older pending row. That row
    keeps its ``chosen_slot_id`` too, so a query that forgot ``status`` would
    silently veto a slot the user never rejected.
    """
    cup = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]
    session = _session(db_engine)
    try:
        first = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(first_slot)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        second = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(second_slot)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        await accept_recommendation(
            session,
            recommendation_id=second.recommendation.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        superseded = (
            await session.execute(
                select(Recommendation).where(
                    Recommendation.id == first.recommendation.id
                )
            )
        ).scalar_one()
        assert superseded.status == "superseded"
        assert superseded.chosen_slot_id == first_slot
        assert await get_rejected_slot_ids(
            db=session, home_id=seeded_actor.home_id, item_id=cup
        ) == frozenset()
    finally:
        await session.close()


# --------------------------------------------------------------- accept → up


async def test_accepting_raises_the_slot_for_a_same_category_item(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Accept → the slot gains exactly +10 for another item of the same category
    and its rank position rises.

    The measurement is deliberately taken with the placement held fixed (both
    slots already occupied) and only the preference row added/removed: the
    accepting placement itself flips the slot's ``capacity`` term, which would
    otherwise swamp the +10 and make the assertion measure the wrong thing.
    """
    l1s1 = storage_hierarchy.slots["L1S1"]
    l1s2 = storage_hierarchy.slots["L1S2"]
    cup = storage_hierarchy.items["马克杯"]
    session = _session(db_engine)
    try:
        # Occupy L1S1 with an unrelated item so both utensil slots have
        # active_count == 1 in both measurement runs.
        spare = await _make_item(session, seeded_actor, "备用杯", "utensil")
        await save_placement(
            db=session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=spare.id,
            slot_id=l1s1,
        )

        outcome = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(l1s2)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        await accept_recommendation(
            session,
            recommendation_id=outcome.recommendation.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )

        # A *different* item of the same category — the same item would also
        # hit _history_match and double the signal (see module docstring).
        other = await _make_item(session, seeded_actor, "另一个杯子", "utensil")

        with_pref = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(l1s1)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=other.id,
        )
        idx_with, score_with = _by_id(with_pref.result.candidates, l1s2)

        await session.execute(delete(UserPreference))
        await session.commit()

        without_pref = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(l1s1)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=other.id,
        )
        idx_without, score_without = _by_id(without_pref.result.candidates, l1s2)

        assert score_with - score_without == 10
        assert idx_with < idx_without
    finally:
        await session.close()


async def test_accepting_writes_a_category_scoped_preference(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup = storage_hierarchy.items["马克杯"]
    slot = storage_hierarchy.slots["L1S2"]
    session = _session(db_engine)
    try:
        outcome = await run_recommendation(
            session,
            provider=MockAIProvider(ranking_responses=[_pick(slot)]),
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
        )
        await accept_recommendation(
            session,
            recommendation_id=outcome.recommendation.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        pref = (
            await session.execute(
                select(UserPreference).where(
                    UserPreference.key == PREFERENCE_KEY_PREFERRED_SLOTS
                )
            )
        ).scalar_one()
        entry = pref.value["slots"][str(slot)]
        assert entry["category"] == "utensil"
        assert entry["count"] == 1
    finally:
        await session.close()


async def test_manual_placement_writes_the_same_preference(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """P0.3's manual path is positive feedback too — same writer, same shape."""
    cup = storage_hierarchy.items["马克杯"]
    slot = storage_hierarchy.slots["L1S1"]
    session = _session(db_engine)
    try:
        await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup,
            slot_id=slot,
        )
        pref = (
            await session.execute(
                select(UserPreference).where(
                    UserPreference.key == PREFERENCE_KEY_PREFERRED_SLOTS
                )
            )
        ).scalar_one()
        assert pref.value["slots"][str(slot)]["category"] == "utensil"
    finally:
        await session.close()
