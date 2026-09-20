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
            "X-User-Id": str(seeded_actor.user_id),
            "X-Home-Id": str(uuid.uuid4()),  # foreign home
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
