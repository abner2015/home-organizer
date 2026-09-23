"""Integration tests for the recommendation API (Phase 5).

Covers all four endpoints + the cross-cutting concerns:

- Happy path: recommend → accept; verify Recommendation, AgentTrace, and
  ItemPlacement rows + the response shapes.
- Reject path: recommend → reject; no placement created.
- Patch path: recommend → patch (different slot) → accept; placement.source
  is 'user_manual'.
- Conflict: accept twice / reject then accept → 409.
- Cross-home: item from another home → 404.
- Extra-field rejection on the request bodies.

The endpoint uses the same AIProvider dependency override pattern as
``test_recognition_api.py``.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.providers.mock import MockAIProvider
from app.api.v1.recommendations import _get_ai_provider
from app.db.enums import (
    PlacementSource,
)
from app.main import app
from app.models import AgentTrace, ItemPlacement, Recommendation

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


def _override_provider(mock: MockAIProvider):
    def _get() -> MockAIProvider:
        return mock

    app.dependency_overrides[_get_ai_provider] = _get


# ---------------------------------------------------------------------- recommend


async def test_recommend_endpoint_happy_path(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_response=_cup_payload(str(slot_id), reason="马克杯放在厨房吊柜第1层")
    )
    _override_provider(mock)

    resp = api_client.post(
        f"/api/v1/recommendations/items/{cup_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "answer"
    assert body["status"] == "pending"
    assert body["chosen_slot_id"] == str(slot_id)
    assert body["retries_used"] == 0
    # Top-3 surface: the chosen one is first.
    assert len(body["candidates"]) >= 1
    assert body["candidates"][0]["slot_id"] == str(slot_id)
    assert "马克杯" in body["candidates"][0]["reason"]
    # Regression: the chosen candidate must be flagged, otherwise the UI
    # has to guess and silently falls back to candidates[0].
    assert body["candidates"][0]["is_recommended"] is True
    assert not any(
        c["is_recommended"] for c in body["candidates"][1:]
    ), "only the chosen slot may be flagged"
    # Regression: the location trio the UI renders must be populated.
    assert body["candidates"][0]["section_name"]
    assert body["candidates"][0]["full_path"]

    # Verify DB rows.
    rec_id = uuid.UUID(body["recommendation_id"])
    async with factory() as session:
        rec = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec_id)
            )
        ).scalar_one()
        assert rec.status == "pending"
        assert rec.chosen_slot_id == slot_id
        trace = (
            await session.execute(
                select(AgentTrace).where(AgentTrace.id == rec.agent_trace_id)
            )
        ).scalar_one()
        assert trace.home_id == seeded_actor.home_id
        assert trace.final_status == "success"
        # Step list should include all 9 pipeline states.
        # The terminal ANSWER state is recorded in AgentRunResult.state but
        # doesn't get its own AgentStepResult (the run returns immediately
        # after a successful VERIFY).
        states = {s["state"] for s in trace.steps}
        assert {
            "intake", "understand", "retrieve",
            "candidate_generation", "filter", "rank",
            "decide", "verify",
        } <= states


async def test_recommend_endpoint_failed_pipeline_returns_failed(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """When the pipeline can't find any candidate, state='failed' is returned."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.storage import StorageSlot

    cup_id = storage_hierarchy.items["马克杯"]
    # Wipe all slots so the filter step returns empty.
    async with async_sessionmaker(db_engine, expire_on_commit=False)() as session:
        await session.execute(delete(StorageSlot))
        await session.commit()

    mock = MockAIProvider()  # would have picked L1S1, but no candidates exist
    _override_provider(mock)
    resp = api_client.post(
        f"/api/v1/recommendations/items/{cup_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "failed"
    assert body["chosen_slot_id"] is None
    assert "无符合硬规则的位置" in body["error"]
    assert body["candidates"] == []
    assert mock.call_count == 0


async def test_recommend_endpoint_cross_home_item_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        f"/api/v1/recommendations/items/{uuid.uuid4()}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_recommend_endpoint_rejects_extra_fields(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(ranking_response=_cup_payload(
        str(storage_hierarchy.slots["L1S1"])
    )))
    resp = api_client.post(
        f"/api/v1/recommendations/items/{cup_id}/recommend",
        json={"hacker": True},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------- accept


async def test_accept_endpoint_creates_placement(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    _override_provider(MockAIProvider(ranking_response=_cup_payload(str(slot_id))))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={"note": "ok"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["placement"]["source"] == "ai_recommendation"
    assert body["placement"]["slot_id"] == str(slot_id)
    assert body["placement"]["is_active"] is True
    assert body["placement"]["note"] == "ok"

    # DB: one placement row exists, rec is now 'accepted'.
    async with factory() as session:
        rec = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec_id)
            )
        ).scalar_one()
        assert rec.status == "accepted"
        placements = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.recommendation_id == rec_id
                )
            )
        ).scalars().all()
        assert len(placements) == 1
        assert placements[0].source == PlacementSource.AI_RECOMMENDATION.value


async def test_accept_twice_returns_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    first = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert first.status_code == 200, first.text
    second = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_accept_cross_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A foreign home must not be able to accept our recommendation."""
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers={
            # A valid token, but a home this user is not a member of.
            **seeded_actor.headers(),
            "X-Home-Id": str(uuid.uuid4()),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------- reject


async def test_reject_endpoint_changes_status(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={"note": "I changed my mind"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    async with factory() as session:
        rec = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec_id)
            )
        ).scalar_one()
        assert rec.status == "rejected"
        placements = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.recommendation_id == rec_id
                )
            )
        ).scalars().all()
        assert placements == []  # reject must NOT create a placement


async def test_reject_then_accept_returns_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)
    rej = api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={},
        headers=seeded_actor.headers(),
    )
    assert rej.status_code == 200
    acc = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert acc.status_code == 409


# ---------------------------------------------------------------------- patch


async def test_patch_then_accept_uses_user_manual_source(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    cup_id = storage_hierarchy.items["马克杯"]
    initial_slot = storage_hierarchy.slots["L1S1"]
    new_slot = storage_hierarchy.slots["L1S2"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(initial_slot))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    patch_resp = api_client.patch(
        f"/api/v1/recommendations/{rec_id}",
        json={"chosen_slot_id": str(new_slot), "reason": "lower is easier"},
        headers=seeded_actor.headers(),
    )
    assert patch_resp.status_code == 200, patch_resp.text
    body = patch_resp.json()
    assert body["status"] == "pending"
    assert body["chosen_slot_id"] == str(new_slot)
    # The new slot should be first in the returned candidates list.
    assert body["candidates"][0]["slot_id"] == str(new_slot)

    acc = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert acc.status_code == 200, acc.text
    assert acc.json()["placement"]["source"] == "user_manual"
    assert acc.json()["placement"]["slot_id"] == str(new_slot)

    # Confirm the placement was actually written with the new slot.
    async with factory() as session:
        placement = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.recommendation_id == rec_id
                )
            )
        ).scalar_one()
        assert placement.source == PlacementSource.USER_MANUAL.value
        assert placement.slot_id == new_slot


async def test_patch_cross_home_slot_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_session
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    _override_provider(MockAIProvider(
        ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"]))
    ))
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    # Create a foreign slot.
    from app.db.enums import StorageSectionType, StorageUnitType
    from app.models.room import Room
    from app.models.storage import StorageSection, StorageSlot, StorageUnit

    other_home = uuid.uuid4()
    other_room = Room(
        home_id=other_home, name="Garage", room_type="other", sort_order=1
    )
    db_session.add(other_room)
    await db_session.flush()
    other_unit = StorageUnit(
        room_id=other_room.id,
        name="Foreign",
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
        label="X",
        allowed_categories=["misc"],
        sort_order=1,
    )
    db_session.add(other_slot)
    await db_session.commit()

    resp = api_client.patch(
        f"/api/v1/recommendations/{rec_id}",
        json={"chosen_slot_id": str(other_slot.id)},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ------------------------------------------------------- reasons + feedback (P0.4)


def _has_ascii_letter(text: str) -> bool:
    return any("A" <= ch <= "z" for ch in text)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


async def test_every_returned_candidate_has_a_chinese_reason(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """P0.4 acceptance: every candidate carries a non-empty, code-free,
    English-free Chinese reason — not just the chosen one."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    _override_provider(
        MockAIProvider(ranking_response=_cup_payload(str(slot_id)))
    )
    resp = api_client.post(
        f"/api/v1/recommendations/items/{cup_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    candidates = resp.json()["candidates"]
    assert candidates
    for c in candidates:
        assert c["reason"], c
        assert _has_cjk(c["reason"])
        assert not _has_ascii_letter(c["reason"]), c["reason"]


async def test_rejected_slot_is_excluded_from_the_next_recommendation(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    rejected = storage_hierarchy.slots["L1S1"]
    fallback = storage_hierarchy.slots["L1S2"]
    cup_id = storage_hierarchy.items["马克杯"]

    _override_provider(
        MockAIProvider(ranking_response=_cup_payload(str(rejected)))
    )
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    reject = api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={"note": "位置太远"},
        headers=seeded_actor.headers(),
    )
    assert reject.status_code == 200, reject.text

    _override_provider(
        MockAIProvider(ranking_response=_cup_payload(str(fallback)))
    )
    resp = api_client.post(
        f"/api/v1/recommendations/items/{cup_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chosen_slot_id"] == str(fallback)
    assert str(rejected) not in [c["slot_id"] for c in body["candidates"]]


async def test_accepting_boosts_the_slot_for_a_same_category_item(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """P0.4 acceptance, over HTTP: after accepting, another item of the same
    category sees that slot score exactly 10 higher and rank higher.

    Uses a *different* item (the same item would also gain a history signal)
    and both utensil slots pre-occupied, so the only variable is the
    preference row.
    """
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import Item
    from app.models.preference import UserPreference

    l1s1 = storage_hierarchy.slots["L1S1"]
    l1s2 = storage_hierarchy.slots["L1S2"]
    cup_id = storage_hierarchy.items["马克杯"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        spare = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="备用杯",
            category="utensil",
            estimated_size="small",
            created_by=seeded_actor.user_id,
        )
        other = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="另一个杯子",
            category="utensil",
            estimated_size="small",
            created_by=seeded_actor.user_id,
        )
        session.add_all([spare, other])
        await session.commit()
        other_id = other.id

    from app.tools.write_tools import save_placement

    async with factory() as session:
        await save_placement(
            db=session,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=spare.id,
            slot_id=l1s1,
        )

    # Accept a recommendation for the cup that lands in L1S2.
    _override_provider(
        MockAIProvider(ranking_response=_cup_payload(str(l1s2)))
    )
    rec_id = _recommend(api_client, seeded_actor, cup_id)
    accept = api_client.post(
        f"/api/v1/recommendations/{rec_id}/accept",
        json={},
        headers=seeded_actor.headers(),
    )
    assert accept.status_code == 200, accept.text

    def _scores() -> dict[str, int]:
        """slot_id → det_score for one run's returned candidates.

        The response is `_top3`-ordered (chosen first), so an index here is
        not a score rank; the positional claim is asserted in
        ``tests/unit/test_feedback_loop.py`` against the true ranked list.
        """
        assert resp.status_code == 200, resp.text
        return {c["slot_id"]: int(c["score"]) for c in resp.json()["candidates"]}

    _override_provider(MockAIProvider(ranking_response=_cup_payload(str(l1s1))))
    resp = api_client.post(
        f"/api/v1/recommendations/items/{other_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    with_pref = _scores()

    async with factory() as session:
        await session.execute(delete(UserPreference))
        await session.commit()

    resp = api_client.post(
        f"/api/v1/recommendations/items/{other_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    without_pref = _scores()

    assert with_pref[str(l1s2)] - without_pref[str(l1s2)] == 10
    # The boost is enough to overtake the sibling slot it used to tie with.
    assert with_pref[str(l1s2)] > with_pref[str(l1s1)]
    assert without_pref[str(l1s2)] == without_pref[str(l1s1)]


# ---------------------------------------------------------------------- revoke


async def test_revoke_endpoint_happy_path(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Reject then revoke → 200, body.status='revoked', the row survives.

    The original reject's note is preserved on candidates[0].audit_note so
    the user can see "I rejected this for X, then changed my mind" in the
    audit trail.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"])))
    _override_provider(mock)
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    rejected = api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={"note": "再想想"},
        headers=seeded_actor.headers(),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "rejected"

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["recommendation_id"] == str(rec_id)
    assert body["status"] == "revoked"

    # The persisted row's audit_note is intact — revoke is an un-do, not
    # an overwrite. The CandidateView doesn't expose it; the unit test
    # checks the raw row, here we just confirm the row still exists and
    # the new status is reflected on the GET.
    get = api_client.get(
        f"/api/v1/recommendations/{rec_id}", headers=seeded_actor.headers()
    )
    assert get.status_code == 200, get.text
    assert get.json()["status"] == "revoked"


async def test_revoke_pending_is_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Revoking a pending recommendation is meaningless — only rejected rows
    can be un-done."""
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"])))
    _override_provider(mock)
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "conflict"
    assert resp.json()["error"]["details"]["status"] == "pending"


async def test_revoke_already_revoked_is_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Revoking twice is refused — keeps the audit story clean."""
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"])))
    _override_provider(mock)
    rec_id = _recommend(api_client, seeded_actor, cup_id)

    api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={"note": "no"},
        headers=seeded_actor.headers(),
    )
    first = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers=seeded_actor.headers(),
    )
    assert first.status_code == 200, first.text
    second = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers=seeded_actor.headers(),
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "conflict"


async def test_revoke_cross_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """A recommendation belonging to another home surfaces as 404, never 403."""
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"])))
    _override_provider(mock)
    rec_id = _recommend(api_client, seeded_actor, cup_id)
    api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={},
        headers=seeded_actor.headers(),
    )

    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


async def test_revoke_unknown_id_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        f"/api/v1/recommendations/{uuid.uuid4()}/revoke",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404, resp.text


async def test_revoke_without_credentials_is_401(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    mock = MockAIProvider(ranking_response=_cup_payload(str(storage_hierarchy.slots["L1S1"])))
    _override_provider(mock)
    rec_id = _recommend(api_client, seeded_actor, cup_id)
    api_client.post(
        f"/api/v1/recommendations/{rec_id}/reject",
        json={},
        headers=seeded_actor.headers(),
    )
    resp = api_client.post(
        f"/api/v1/recommendations/{rec_id}/revoke",
        json={},
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------- helpers


def _recommend(
    api_client: TestClient,
    seeded_actor,
    item_id: uuid.UUID,
) -> uuid.UUID:
    """Helper: call POST /recommend and return the new recommendation id."""
    resp = api_client.post(
        f"/api/v1/recommendations/items/{item_id}/recommend",
        json={},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["recommendation_id"])
