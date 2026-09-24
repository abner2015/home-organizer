"""Placement service — accept / reject / patch + manual place / unplace.

Unit-level tests using the real SQLite DB + the real agent + a mock AI
provider. Covers the business invariants:

- accept creates exactly one ItemPlacement row.
- accept closes whatever active placement the item already had.
- accept sets source = ai_recommendation (no prior PATCH) or user_manual (after PATCH).
- accept twice on the same recommendation → 409 (second call's row isn't pending).
- reject on a pending → 200, no placement created.
- reject then accept on the same recommendation → 409 (status changed).
- PATCH then accept creates a user_manual placement.
- PATCH to a slot in another home → 404.
- accept / reject on a recommendation belonging to another home → 404.
- place_item / unplace_item: the manual path (P0.3), with no Recommendation
  writes and soft-close removal.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.placement_service import (
    accept_recommendation,
    bulk_revoke,
    patch_recommendation,
    place_item,
    reject_recommendation,
    revoke_recommendation,
    unplace_item,
    update_placement,
)
from app.ai.providers.mock import MockAIProvider
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.db.enums import (
    PlacementSource,
    RecommendationStatus,
    StorageSectionType,
    StorageUnitType,
)
from app.models import AgentTrace, Item, ItemPlacement, Recommendation, StorageSlot
from app.services.recommendation_service import run_recommendation

pytestmark = pytest.mark.asyncio


def _cup_payload(slot_id: str, *, reason: str = "马克杯放在厨房吊柜") -> dict:
    return {
        "candidates": [
            {
                "slot_id": slot_id,
                "confidence": 0.9,
                "reason": reason,
                "matched_rules": [],
                "evidence_item_ids": [],
            }
        ]
    }


async def _run_recommendation(
    db_engine, seeded_actor, storage_hierarchy, item_id
) -> Recommendation:
    """Run the agent end-to-end and return the persisted Recommendation row."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        provider = MockAIProvider(
            ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
        )
        outcome = await run_recommendation(
            session,
            provider=provider,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=item_id,
        )
        return outcome.recommendation


# ---------------------------------------------------------------------- happy path


async def test_accept_creates_placement_with_ai_source(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Accepting an un-patched recommendation creates an ai_recommendation placement."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        outcome = await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            note="looks good",
        )
        assert outcome.recommendation.status == RecommendationStatus.ACCEPTED.value
        assert outcome.was_patched is False
        assert outcome.placement.source == PlacementSource.AI_RECOMMENDATION.value
        assert outcome.placement.item_id == cup_id
        assert outcome.placement.recommendation_id == rec.id
        assert outcome.placement.note == "looks good"


async def test_accept_after_patch_uses_user_manual_source(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """PATCH then ACCEPT → placement.source=user_manual."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    new_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        await patch_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            chosen_slot_id=new_slot,
            reason="I want it lower",
        )

    async with _session(db_engine) as session:
        outcome = await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        assert outcome.was_patched is True
        assert outcome.placement.source == PlacementSource.USER_MANUAL.value
        assert outcome.placement.slot_id == new_slot


# ---------------------------------------------------------------------- errors


async def test_accept_twice_returns_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await accept_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
            )


async def test_reject_then_accept_returns_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            note="not here",
        )
    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await accept_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
            )


async def test_reject_does_not_create_placement(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        outcome = await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            note="nope",
        )
        assert outcome.recommendation.status == RecommendationStatus.REJECTED.value
        # Verify no ItemPlacement row was created.
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
        assert rows == []


async def test_accept_cross_home_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Recommendation belongs to home A; caller is home B → NotFoundError."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    other_home = uuid.uuid4()

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await accept_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=other_home,
                user_id=seeded_actor.user_id,
            )


async def test_patch_cross_home_slot_is_404(
    seeded_actor, db_engine, storage_hierarchy, db_session
) -> None:
    """The user PATCHes to a slot in another home → NotFoundError."""
    from app.models.room import Room
    from app.models.storage import StorageSection, StorageUnit

    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    # Create a foreign slot under a foreign home.
    other_home_id = uuid.uuid4()
    other_room = Room(
        home_id=other_home_id,
        name="Garage",
        room_type="other",
        sort_order=1,
    )
    db_session.add(other_room)
    await db_session.flush()
    other_unit = StorageUnit(
        room_id=other_room.id,
        name="Foreign Shelf",
        unit_type=StorageUnitType.CABINET.value,
        sort_order=1,
    )
    db_session.add(other_unit)
    await db_session.flush()
    other_section = StorageSection(
        unit_id=other_unit.id,
        name="S1",
        section_type=StorageSectionType.LAYER.value,
        sort_order=1,
    )
    db_session.add(other_section)
    await db_session.flush()
    other_slot = StorageSlot(
        section_id=other_section.id,
        code="X1",
        label="Foreign",
        allowed_categories=["misc"],
        sort_order=1,
    )
    db_session.add(other_slot)
    await db_session.commit()
    foreign_slot_id = other_slot.id

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await patch_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
                chosen_slot_id=foreign_slot_id,
                reason="trying to steal this",
            )


async def test_patch_then_accept_creates_only_one_placement(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Multiple PATCH calls (overwrite) should still result in one placement."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await patch_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            chosen_slot_id=storage_hierarchy.slots["L1S2"],
            reason="first choice",
        )
    async with _session(db_engine) as session:
        await patch_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            chosen_slot_id=storage_hierarchy.slots["L1S1"],
            reason="second choice",
        )
    async with _session(db_engine) as session:
        outcome = await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
    assert outcome.placement.slot_id == storage_hierarchy.slots["L1S1"]
    # Exactly one placement row exists for the item.
    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
        assert len(rows) == 1


async def test_supersedes_prior_pending_on_accept(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Re-running the pipeline produces a new pending recommendation; accepting
    the new one should flip the old one to 'superseded'."""
    cup_id = storage_hierarchy.items["马克杯"]
    old_rec = await _run_recommendation(
        db_engine, seeded_actor, storage_hierarchy, cup_id
    )
    new_rec = await _run_recommendation(
        db_engine, seeded_actor, storage_hierarchy, cup_id
    )
    assert new_rec.id != old_rec.id

    async with _session(db_engine) as session:
        await accept_recommendation(
            session,
            recommendation_id=new_rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
    async with _session(db_engine) as session:
        old_row = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == old_rec.id)
            )
        ).scalar_one()
        assert old_row.status == RecommendationStatus.SUPERSEDED.value
        new_row = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == new_rec.id)
            )
        ).scalar_one()
        assert new_row.status == RecommendationStatus.ACCEPTED.value


async def test_failed_recommendation_cannot_be_accepted(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A FAILED recommendation (chosen_slot_id=None) should reject accept."""
    from app.db.enums import AgentTraceStatus
    from app.models import AgentTrace

    cup_id = storage_hierarchy.items["马克杯"]
    # Build a "failed" Recommendation by hand: trace + rec with chosen_slot_id=None.
    async with _session(db_engine) as session:
        trace = AgentTrace(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            steps=[],
            final_status=AgentTraceStatus.VERIFIER_FAILED.value,
            total_duration_ms=0,
            error="无符合硬规则的位置",
        )
        session.add(trace)
        await session.flush()
        rec = Recommendation(
            item_id=cup_id,
            agent_trace_id=trace.id,
            candidates=[],
            chosen_slot_id=None,
            status=RecommendationStatus.PENDING.value,
        )
        session.add(rec)
        await session.commit()
        rec_id = rec.id

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await accept_recommendation(
                session,
                recommendation_id=rec_id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
            )


# ------------------------------------------------------------- manual placement


async def test_accept_closes_previous_active_placement(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Regression: accepting a recommendation must move the item, not fork it.

    The accept path used to insert an ItemPlacement without closing the active
    one. On PostgreSQL that raised IntegrityError (500); on SQLite — which
    never enforced the partial unique index — it silently produced two active
    rows and the item view picked whichever came last.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    manual_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        manual = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=manual_slot,
        )
        manual_id = manual.id

    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    async with _session(db_engine) as session:
        outcome = await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()

    active = [r for r in rows if r.removed_at is None]
    assert len(rows) == 2, "the manual row is history, not garbage"
    assert len(active) == 1
    assert active[0].id == outcome.placement.id
    assert active[0].slot_id == storage_hierarchy.slots["L1S1"]
    closed = next(r for r in rows if r.id == manual_id)
    assert closed.removed_at is not None


async def test_place_item_writes_no_recommendation_rows(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A manual placement does not resolve, supersede or reject any suggestion."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S2"],
            note="我直接放进去了",
        )
        assert placement.source == PlacementSource.USER_MANUAL.value
        assert placement.recommendation_id is None
        assert placement.note == "我直接放进去了"

    async with _session(db_engine) as session:
        row = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec.id)
            )
        ).scalar_one()
        assert row.status == RecommendationStatus.PENDING.value


async def test_place_item_then_accept_still_works(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """The manual path must not make the item un-recommendable."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S2"],
        )

    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    async with _session(db_engine) as session:
        outcome = await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        assert outcome.recommendation.status == RecommendationStatus.ACCEPTED.value


async def test_close_active_placements_uses_the_aware_utc_helper(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """The close timestamp must come from ``utc_now``, not ``datetime.utcnow``.

    Naive values serialise without an offset, and the Web client's
    ``new Date(iso)`` then reads them as *local* time — every "moved out at"
    label shifts by the viewer's UTC offset. SQLite drops the offset on read,
    so the only place this is observable is the value handed to the UPDATE.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    sentinel = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    async with _session(db_engine) as session:
        await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S2"],
        )

    from app.tools import write_tools

    with mock.patch.object(write_tools, "utc_now", return_value=sentinel):
        async with _session(db_engine) as session:
            await place_item(
                session,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                item_id=cup_id,
                slot_id=storage_hierarchy.slots["L1S1"],
            )

    async with _session(db_engine) as session:
        closed = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.item_id == cup_id,
                    ItemPlacement.removed_at.is_not(None),
                )
            )
        ).scalar_one()
    assert closed.removed_at.replace(tzinfo=UTC) == sentinel


async def test_unplace_item_soft_closes(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        closed = await unplace_item(
            session, placement_id=placement_id, home_id=seeded_actor.home_id
        )
        assert closed.removed_at is not None

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.id == placement_id)
            )
        ).scalars().all()
        assert len(rows) == 1, "soft close must not delete the row"


async def test_unplace_item_twice_raises_conflict(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        await unplace_item(
            session, placement_id=placement_id, home_id=seeded_actor.home_id
        )

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await unplace_item(
                session, placement_id=placement_id, home_id=seeded_actor.home_id
            )


async def test_unplace_placement_of_another_home_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """The placement exists, but its item belongs to a different home → 404."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await unplace_item(
                session, placement_id=placement_id, home_id=uuid.uuid4()
            )


async def test_unplace_unknown_placement_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await unplace_item(
                session, placement_id=uuid.uuid4(), home_id=seeded_actor.home_id
            )


async def test_place_item_cross_home_slot_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    from app.models.room import Room
    from app.models.storage import StorageSection, StorageUnit

    other_room = Room(
        home_id=uuid.uuid4(), name="Garage", room_type="other", sort_order=1
    )
    async with _session(db_engine) as session:
        session.add(other_room)
        await session.flush()
        other_unit = StorageUnit(
            room_id=other_room.id,
            name="Foreign",
            unit_type=StorageUnitType.CABINET.value,
            sort_order=1,
        )
        session.add(other_unit)
        await session.flush()
        other_section = StorageSection(
            unit_id=other_unit.id,
            name="S1",
            section_type=StorageSectionType.LAYER.value,
            sort_order=1,
        )
        session.add(other_section)
        await session.flush()
        other_slot = StorageSlot(
            section_id=other_section.id,
            code="X1",
            label="Foreign",
            allowed_categories=["misc"],
            sort_order=1,
        )
        session.add(other_slot)
        await session.commit()
        foreign_slot_id = other_slot.id

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await place_item(
                session,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                item_id=storage_hierarchy.items["马克杯"],
                slot_id=foreign_slot_id,
            )


async def test_place_item_translates_integrity_error_to_conflict(
    db_engine, seeded_actor, storage_hierarchy
) -> None:
    """The partial unique index ``uq_item_placements_one_active_per_item`` makes
    the "one item, one active slot" rule a DB invariant. Two concurrent
    POST /placements both run the close-then-insert sequence; the loser's
    flush collides with the winner's already-committed row and PG raises
    IntegrityError. The writer translates that to ConflictError (→ 409 at the
    HTTP boundary) and rolls the session back so the caller's next request
    starts clean.

    SQLite never enforces the partial unique index, so we simulate the race
    by monkeypatching the session's flush to raise the same IntegrityError
    the index would. The production path is the same after the patch."""
    from sqlalchemy.exc import IntegrityError

    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]

    async def _make_failing_flush(_self):
        raise IntegrityError(
            "INSERT INTO item_placements ... uq_item_placements_one_active_per_item",
            params=None,
            orig=Exception(
                "duplicate key value violates unique constraint "
                '"uq_item_placements_one_active_per_item"'
            ),
        )

    async with _session(db_engine) as session:
        with mock.patch.object(
            type(session), "flush", new=_make_failing_flush
        ):
            with pytest.raises(ConflictError) as exc_info:
                await place_item(
                    session,
                    home_id=seeded_actor.home_id,
                    user_id=seeded_actor.user_id,
                    item_id=cup_id,
                    slot_id=slot_id,
                )
    assert "already being placed" in str(exc_info.value)
    assert exc_info.value.details.get("constraint") == (
        "uq_item_placements_one_active_per_item"
    )

    # No row should have been committed — the failed flush never reached
    # place_item's commit, and the explicit rollback in _create_placement
    # also cleared the staged insert.
    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
    assert rows == [], "failed placement must not leave any row behind"


# -------------------------------------------------------------- revoke (P0.6)


async def test_revoke_rejection_happy_path(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A rejected recommendation can be revoked — status flips to 'revoked'."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            note="再想想",
        )

    async with _session(db_engine) as session:
        outcome = await revoke_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
        )
    assert outcome.recommendation.status == RecommendationStatus.REVOKED.value

    # The row is preserved (status flipped, not deleted) so the audit trail
    # — the original reject's reason on candidates[0].audit_note — survives.
    async with _session(db_engine) as session:
        reloaded = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec.id)
            )
        ).scalar_one()
    assert reloaded.status == RecommendationStatus.REVOKED.value
    cands = list(reloaded.candidates or [])
    assert cands and isinstance(cands[0], dict)
    assert cands[0].get("audit_note") == "再想想"


async def test_revoke_pending_is_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A 'pending' recommendation is not yet excluded; revoking is meaningless."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await revoke_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
            )


async def test_revoke_already_revoked_is_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Revoking twice is a no-op the API refuses — keeps the audit story clean."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
        )
        await revoke_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
        )

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await revoke_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
            )


async def test_revoke_accepted_is_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """An accepted recommendation has an ItemPlacement; un-placing is a
    separate flow (``DELETE /placements/{id}``), not revoke."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await accept_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await revoke_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=seeded_actor.home_id,
            )


async def test_revoke_cross_home_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A recommendation belonging to another home surfaces as 404, not 403."""
    other_home_id = uuid.uuid4()
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
        )

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await revoke_recommendation(
                session,
                recommendation_id=rec.id,
                home_id=other_home_id,
            )


# ----------------------------------------------------------------- bulk-revoke (P0.B)


async def test_bulk_revoke_rejects_all_succeed(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """All-rejected batch — every row flips to 'revoked', errors empty."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec1 = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    rec2 = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    rec3 = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)

    for r in (rec1, rec2, rec3):
        async with _session(db_engine) as session:
            await reject_recommendation(
                session,
                recommendation_id=r.id,
                home_id=seeded_actor.home_id,
            )

    async with _session(db_engine) as session:
        outcome = await bulk_revoke(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            recommendation_ids=[rec1.id, rec2.id, rec3.id],
            auto_rerun=False,
            provider=MockAIProvider(),
        )

    assert [r.id for r in outcome.revoked] == [rec1.id, rec2.id, rec3.id]
    assert outcome.errors == []
    assert outcome.rerun_results == []

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(Recommendation).where(
                    Recommendation.id.in_([rec1.id, rec2.id, rec3.id])
                )
            )
        ).scalars().all()
    assert all(r.status == RecommendationStatus.REVOKED.value for r in rows)


async def test_bulk_revoke_partial_failure_collects_errors(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Mix of valid + cross-home recs: only the valid one revokes; the bad
    ones land in ``errors[]`` with ``code='not_found'`` — no top-level raise.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    good = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=good.id,
            home_id=seeded_actor.home_id,
        )

    bogus = uuid.uuid4()  # never created
    async with _session(db_engine) as session:
        outcome = await bulk_revoke(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            recommendation_ids=[good.id, bogus],
            auto_rerun=False,
            provider=MockAIProvider(),
        )

    assert [r.id for r in outcome.revoked] == [good.id]
    assert len(outcome.errors) == 1
    assert outcome.errors[0]["code"] == "not_found"
    assert outcome.errors[0]["recommendation_id"] == str(bogus)


async def test_bulk_revoke_already_revoked_is_error_not_500(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """A rec that's already revoked is a per-row conflict — not a top-level 500."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    async with _session(db_engine) as session:
        await reject_recommendation(
            session, recommendation_id=rec.id, home_id=seeded_actor.home_id
        )
        await revoke_recommendation(
            session, recommendation_id=rec.id, home_id=seeded_actor.home_id
        )

    async with _session(db_engine) as session:
        outcome = await bulk_revoke(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            recommendation_ids=[rec.id],
            auto_rerun=False,
            provider=MockAIProvider(),
        )
    assert outcome.revoked == []
    assert len(outcome.errors) == 1
    assert outcome.errors[0]["code"] == "conflict"
    assert outcome.errors[0]["recommendation_id"] == str(rec.id)


async def test_bulk_revoke_with_auto_rerun_creates_new_recommendation(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """auto_rerun=True re-runs the pipeline for the affected item and returns
    the new Recommendation in ``rerun_results[]``."""
    cup_id = storage_hierarchy.items["马克杯"]
    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    async with _session(db_engine) as session:
        await reject_recommendation(
            session, recommendation_id=rec.id, home_id=seeded_actor.home_id
        )

    async with _session(db_engine) as session:
        outcome = await bulk_revoke(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            recommendation_ids=[rec.id],
            auto_rerun=True,
            provider=MockAIProvider(
                ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
            ),
        )

    assert len(outcome.rerun_results) == 1
    item_id, rerun, _err = outcome.rerun_results[0]
    assert item_id == cup_id
    assert rerun is not None
    assert rerun.ok
    assert rerun.recommendation.id != rec.id
    # The fresh rec is in pending state (the pipeline starts fresh).
    assert rerun.recommendation.status == RecommendationStatus.PENDING.value


async def test_revoked_slot_reappears_in_next_recommend(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """End-to-end: recommend → reject → revoke → recommend again.

    The MockAIProvider proposes the same slot each time (the ranker is
    deterministic). After the user rejects, the next pipeline call would
    normally FILTER the slot out via ``get_rejected_slot_ids``; after revoke,
    the same call must surface it again. Asserts the exclusion set
    (read-side) clears on status flip, without any code change there.
    """
    from app.tools.recommendation_tools import get_rejected_slot_ids

    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]

    rec = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    assert str(rec.chosen_slot_id) == str(slot_id)

    async with _session(db_engine) as session:
        await reject_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
            note="first pass",
        )
    # While rejected, the slot is in the exclusion set.
    async with _session(db_engine) as session:
        excluded = await get_rejected_slot_ids(
            db=session,
            home_id=seeded_actor.home_id,
            item_id=cup_id,
        )
    assert slot_id in excluded

    async with _session(db_engine) as session:
        await revoke_recommendation(
            session,
            recommendation_id=rec.id,
            home_id=seeded_actor.home_id,
        )
    # After revoke, the exclusion set is empty again — the slot is back.
    async with _session(db_engine) as session:
        excluded_after = await get_rejected_slot_ids(
            db=session,
            home_id=seeded_actor.home_id,
            item_id=cup_id,
        )
    assert excluded_after == frozenset(), (
        "revoke must drop the slot from get_rejected_slot_ids without any "
        "code change on the read path"
    )

    # And a fresh recommend pipeline now produces a recommendation whose
    # chosen slot is back in play. (The mock still proposes L1S1.)
    rec2 = await _run_recommendation(db_engine, seeded_actor, storage_hierarchy, cup_id)
    assert str(rec2.chosen_slot_id) == str(slot_id)
    assert rec2.id != rec.id
    assert rec2.status == RecommendationStatus.PENDING.value


# --------------------------------------------------------------- update (P0.7)


async def test_update_placement_edits_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Only `note` provided → row UPDATE, slot stays the same."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
            note="old note",
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        updated = await update_placement(
            session,
            placement_id=placement_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_note="new note",
        )
        assert updated.note == "new note"
        assert updated.slot_id == storage_hierarchy.slots["L1S1"]
        assert updated.id == placement_id, "edit-note must not insert a new row"

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == placement_id
        assert rows[0].note == "new note"


async def test_update_placement_clears_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """`note=None` → clear the existing note."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
            note="to be cleared",
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        updated = await update_placement(
            session,
            placement_id=placement_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_note=None,
        )
        assert updated.note is None


async def test_update_placement_moves_slot(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """`slot_id` to a different slot → old row soft-closed, new row active."""
    cup_id = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=first_slot,
        )
        old_id = placement.id

    async with _session(db_engine) as session:
        moved = await update_placement(
            session,
            placement_id=old_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_slot_id=second_slot,
        )
        assert moved.id != old_id
        assert moved.slot_id == second_slot
        assert moved.source == PlacementSource.USER_MANUAL.value
        assert moved.recommendation_id is None

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
    assert len(rows) == 2
    closed = [r for r in rows if r.removed_at is not None]
    active = [r for r in rows if r.removed_at is None]
    assert len(active) == 1
    assert len(closed) == 1
    assert active[0].id == moved.id
    assert active[0].slot_id == second_slot
    assert closed[0].id == old_id


async def test_update_placement_move_inherits_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Only `slot_id` provided → new row's note = old row's note (P0.7 design)."""
    cup_id = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=first_slot,
            note="keep me",
        )
        old_id = placement.id

    async with _session(db_engine) as session:
        moved = await update_placement(
            session,
            placement_id=old_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_slot_id=second_slot,
        )
        assert moved.note == "keep me"


async def test_update_placement_both_fields_uses_new_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Both `note` and `slot_id` provided → new row's note = explicit new value."""
    cup_id = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=first_slot,
            note="old",
        )
        old_id = placement.id

    async with _session(db_engine) as session:
        moved = await update_placement(
            session,
            placement_id=old_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_slot_id=second_slot,
            set_note="explicit new",
        )
        assert moved.slot_id == second_slot
        assert moved.note == "explicit new"


async def test_update_placement_both_clear_note(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Move + `note=None` → new row has no note (explicit clear wins)."""
    cup_id = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=first_slot,
            note="to clear",
        )
        old_id = placement.id

    async with _session(db_engine) as session:
        moved = await update_placement(
            session,
            placement_id=old_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_slot_id=second_slot,
            set_note=None,
        )
        assert moved.note is None


async def test_update_placement_same_slot_is_noop(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """`slot_id` equal to the current slot → no new row, just edit note."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot = storage_hierarchy.slots["L1S1"]

    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=slot,
            note="original",
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        # Pass both fields; same-slot move is a no-op for the slot.
        updated = await update_placement(
            session,
            placement_id=placement_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            set_slot_id=slot,
            set_note="changed",
        )
        assert updated.id == placement_id
        assert updated.note == "changed"

    async with _session(db_engine) as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
        assert len(rows) == 1, "same-slot PATCH must not insert a new row"


async def test_update_placement_closed_is_409(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Editing a placement with `removed_at` set → ConflictError."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id
        await unplace_item(
            session, placement_id=placement_id, home_id=seeded_actor.home_id
        )

    async with _session(db_engine) as session:
        with pytest.raises(ConflictError):
            await update_placement(
                session,
                placement_id=placement_id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                set_note="should fail",
            )


async def test_update_placement_cross_home_is_404(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Placement's item belongs to home A; caller is home B → NotFoundError."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await update_placement(
                session,
                placement_id=placement_id,
                home_id=uuid.uuid4(),
                user_id=seeded_actor.user_id,
                set_note="x",
            )


async def test_update_placement_unknown_id_is_404(
    seeded_actor, db_engine
) -> None:
    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await update_placement(
                session,
                placement_id=uuid.uuid4(),
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                set_note="x",
            )


async def test_update_placement_no_fields_is_422_at_service(
    seeded_actor, db_engine, storage_hierarchy
) -> None:
    """Both sentinels (route layer would 422 first) → service still defends."""
    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    async with _session(db_engine) as session:
        with pytest.raises(ValidationFailedError):
            await update_placement(
                session,
                placement_id=placement_id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
            )


async def test_update_placement_cross_home_slot_is_404(
    seeded_actor, db_engine, storage_hierarchy, db_session
) -> None:
    """The user PATCHes to a slot in another home → NotFoundError."""
    from app.models.room import Room
    from app.models.storage import StorageSection, StorageUnit

    cup_id = storage_hierarchy.items["马克杯"]
    async with _session(db_engine) as session:
        placement = await place_item(
            session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            slot_id=storage_hierarchy.slots["L1S1"],
        )
        placement_id = placement.id

    # Build a foreign slot in a foreign home.
    other_home_id = uuid.uuid4()
    other_room = Room(
        home_id=other_home_id, name="Garage", room_type="other", sort_order=1
    )
    db_session.add(other_room)
    await db_session.flush()
    other_unit = StorageUnit(
        room_id=other_room.id,
        name="Foreign Shelf",
        unit_type=StorageUnitType.CABINET.value,
        sort_order=1,
    )
    db_session.add(other_unit)
    await db_session.flush()
    other_section = StorageSection(
        unit_id=other_unit.id,
        name="S1",
        section_type=StorageSectionType.LAYER.value,
        sort_order=1,
    )
    db_session.add(other_section)
    await db_session.flush()
    other_slot = StorageSlot(
        section_id=other_section.id,
        code="X1",
        label="Foreign",
        allowed_categories=["misc"],
        sort_order=1,
    )
    db_session.add(other_slot)
    await db_session.commit()
    foreign_slot_id = other_slot.id

    async with _session(db_engine) as session:
        with pytest.raises(NotFoundError):
            await update_placement(
                session,
                placement_id=placement_id,
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                set_slot_id=foreign_slot_id,
            )


# ---------------------------------------------------------------------- helpers


def _session(db_engine):  # type: ignore[no-untyped-def]
    """Return an async-context-manager that opens a fresh session."""
    return _SessionHolder(db_engine)


class _SessionHolder:
    def __init__(self, engine) -> None:  # type: ignore[no-untyped-def]
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        self._sess = self._factory()
        return self._sess

    async def __aexit__(self, *exc: object) -> None:
        await self._sess.close()


# Silence unused-import warnings while keeping the symbols handy.
_ = (Item, AgentTrace, StorageSlot, MockAIProvider)
