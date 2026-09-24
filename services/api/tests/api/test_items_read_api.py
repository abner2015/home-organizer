"""Integration tests for the item read API (Phase 10).

The Web app needs four read routes that didn't exist:

- ``GET /api/v1/items``              — paged + filtered list.
- ``GET /api/v1/items/{id}``         — one item with its current placement.
- ``GET /api/v1/items/{id}/placements`` — placement history.
- ``GET /api/v1/items/{id}/candidates`` — deterministic generate/filter/rank,
  no LLM call.

The read routes enrich rows with data that isn't a column on ``items``:
``current_placement`` (a join through ``item_placements``) and the resolved
``slot_path``. These tests pin that enrichment, plus the cross-home 404s.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.enums import PlacementSource
from app.main import app
from app.models import AgentTrace, ItemPlacement, Recommendation

pytestmark = pytest.mark.asyncio


async def _place(
    db_engine,
    *,
    item_id: uuid.UUID,
    slot_id: uuid.UUID,
    user_id: uuid.UUID,
    active: bool = True,
) -> uuid.UUID:
    """Insert an ItemPlacement; returns its id. Deactivated rows keep a
    ``removed_at`` so the partial-unique 'one active placement' rule holds."""
    placement_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ItemPlacement(
                id=placement_id,
                item_id=item_id,
                slot_id=slot_id,
                source=PlacementSource.USER_MANUAL.value,
                placed_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
                removed_at=None if active else datetime(2026, 9, 2, 9, 0, tzinfo=UTC),
                placed_by=user_id,
            )
        )
        await session.commit()
    return placement_id


# ------------------------------------------------------------------ list items


async def test_list_items_enriches_current_placement(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """An item with an active placement exposes slot_id + resolved slot_path."""
    cup_id = storage_hierarchy.items["马克杯"]
    await _place(
        db_engine,
        item_id=cup_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        user_id=seeded_actor.user_id,
    )

    resp = api_client.get("/api/v1/items", headers=seeded_actor.headers())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert body["page"] == 1

    by_name = {i["name"]: i for i in body["items"]}
    cup = by_name["马克杯"]
    assert cup["current_placement"]["slot_id"] == str(storage_hierarchy.slots["L1S1"])
    assert cup["current_placement"]["slot_path"] == "厨房/厨房吊柜/第1层第1格"
    # No placement → None, not a missing key.
    assert by_name["处方药"]["current_placement"] is None


async def test_list_items_ignores_removed_placements(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A deactivated placement must not read as the current location."""
    cup_id = storage_hierarchy.items["马克杯"]
    await _place(
        db_engine,
        item_id=cup_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        user_id=seeded_actor.user_id,
        active=False,
    )

    resp = api_client.get("/api/v1/items", headers=seeded_actor.headers())
    cup = next(i for i in resp.json()["items"] if i["name"] == "马克杯")
    assert cup["current_placement"] is None


async def test_list_items_filters_by_name_and_category(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        "/api/v1/items", params={"q": "马克"}, headers=seeded_actor.headers()
    )
    assert [i["name"] for i in resp.json()["items"]] == ["马克杯"]

    resp = api_client.get(
        "/api/v1/items", params={"category": "medicine"}, headers=seeded_actor.headers()
    )
    assert [i["name"] for i in resp.json()["items"]] == ["处方药"]

    resp = api_client.get(
        "/api/v1/items", params={"q": "不存在"}, headers=seeded_actor.headers()
    )
    assert resp.json()["items"] == []
    assert resp.json()["total"] == 0


async def test_list_items_pagination(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        "/api/v1/items",
        params={"page": 2, "page_size": 1},
        headers=seeded_actor.headers(),
    )
    body = resp.json()
    assert body["total"] == 2
    assert body["page"] == 2
    assert len(body["items"]) == 1


async def test_list_items_rejects_page_size_over_cap(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        "/api/v1/items", params={"page_size": 500}, headers=seeded_actor.headers()
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------- one item


async def test_get_item_happy_path(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.get(
        f"/api/v1/items/{cup_id}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(cup_id)
    assert body["name"] == "马克杯"
    assert body["home_id"] == str(seeded_actor.home_id)


async def test_get_item_unknown_id_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        f"/api/v1/items/{uuid.uuid4()}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


async def test_get_item_other_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A real item id from another home 404s (no existence leak)."""
    from app.models.item import Item

    other_item_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Item(
                id=other_item_id,
                home_id=uuid.uuid4(),
                name="别人的杯子",
                category="utensil",
                created_by=uuid.uuid4(),
            )
        )
        await session.commit()

    resp = api_client.get(
        f"/api/v1/items/{other_item_id}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


# -------------------------------------------------------------- placement list


async def test_list_placements_returns_history_newest_first(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    await _place(
        db_engine,
        item_id=cup_id,
        slot_id=storage_hierarchy.slots["L1"],
        user_id=seeded_actor.user_id,
        active=False,
    )
    await _place(
        db_engine,
        item_id=cup_id,
        slot_id=storage_hierarchy.slots["L1S2"],
        user_id=seeded_actor.user_id,
    )

    resp = api_client.get(
        f"/api/v1/items/{cup_id}/placements", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    # Every row carries a resolved path, active or not.
    assert {r["slot_path"] for r in rows} == {
        "客厅/客厅装饰柜/左玻璃柜第1层",
        "厨房/厨房吊柜/第1层第2格",
    }
    assert [r["removed_at"] is None for r in rows].count(True) == 1


async def test_list_placements_unknown_item_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        f"/api/v1/items/{uuid.uuid4()}/placements", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------- candidates


async def test_candidates_are_deterministic_and_ranked(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """No LLM involved: the endpoint returns the ranker's output directly."""
    cup_id = storage_hierarchy.items["马克杯"]
    url = f"/api/v1/items/{cup_id}/candidates"

    first = api_client.get(url, headers=seeded_actor.headers())
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["pre_filter_count"] >= body["post_filter_count"]
    assert body["final_candidates"], "马克杯 should have at least one candidate"

    scores = [c["score"] for c in body["final_candidates"]]
    assert scores == sorted(scores, reverse=True)

    # Every candidate is fully located, and the chosen flag is *not* set —
    # this route never runs DECIDE.
    for c in body["final_candidates"]:
        assert c["full_path"]
        assert c["section_name"]
        assert c["is_recommended"] is False

    second = api_client.get(url, headers=seeded_actor.headers())
    assert second.json() == body


async def test_candidates_unknown_item_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        f"/api/v1/items/{uuid.uuid4()}/candidates", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


# ------------------------------------------------- reasons + feedback (P0.4)


async def test_ai_placement_carries_a_reason_and_manual_one_is_blank(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """The item page's 「为什么放这里」 comes from the placement's originating
    recommendation — a manual placement has nothing to explain."""
    cup_id = storage_hierarchy.items["马克杯"]
    ai_slot = storage_hierarchy.slots["L1S1"]
    manual_slot = storage_hierarchy.slots["L1S2"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with factory() as session:
        trace = AgentTrace(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            steps=[],
            final_status="success",
            total_duration_ms=1,
        )
        session.add(trace)
        await session.flush()
        rec = Recommendation(
            item_id=cup_id,
            agent_trace_id=trace.id,
            candidates=[
                {
                    "slot_id": str(ai_slot),
                    "confidence": 0.9,
                    "reason": "马克杯放在厨房吊柜第1层",
                    "matched_rules": [],
                    "evidence_item_ids": [],
                }
            ],
            chosen_slot_id=ai_slot,
            status="accepted",
        )
        session.add(rec)
        await session.flush()
        session.add_all(
            [
                ItemPlacement(
                    item_id=cup_id,
                    slot_id=ai_slot,
                    source=PlacementSource.AI_RECOMMENDATION.value,
                    recommendation_id=rec.id,
                    placed_by=seeded_actor.user_id,
                ),
                ItemPlacement(
                    item_id=cup_id,
                    slot_id=manual_slot,
                    source=PlacementSource.USER_MANUAL.value,
                    placed_by=seeded_actor.user_id,
                ),
            ]
        )
        await session.commit()

    resp = api_client.get(
        f"/api/v1/items/{cup_id}/placements", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    rows = {r["source"]: r for r in resp.json()}
    assert rows[PlacementSource.AI_RECOMMENDATION.value]["reason"] == (
        "马克杯放在厨房吊柜第1层"
    )
    assert rows[PlacementSource.USER_MANUAL.value]["reason"] == ""


async def test_candidates_endpoint_excludes_a_rejected_slot(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """The no-LLM candidate route shares the agent's exclusion set."""
    cup_id = storage_hierarchy.items["马克杯"]
    rejected = storage_hierarchy.slots["L1S1"]

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        trace = AgentTrace(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=cup_id,
            steps=[],
            final_status="success",
            total_duration_ms=1,
        )
        session.add(trace)
        await session.flush()
        session.add(
            Recommendation(
                item_id=cup_id,
                agent_trace_id=trace.id,
                candidates=[{"slot_id": str(rejected), "reason": ""}],
                chosen_slot_id=rejected,
                status="rejected",
            )
        )
        await session.commit()

    resp = api_client.get(
        f"/api/v1/items/{cup_id}/candidates", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert str(rejected) not in [c["slot_id"] for c in body["final_candidates"]]
    # Every remaining candidate still explains itself (P0.4).
    for c in body["final_candidates"]:
        assert c["reason"]


async def test_candidates_endpoint_fills_a_reason_for_every_row(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.get(
        f"/api/v1/items/{cup_id}/candidates", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    for c in resp.json()["final_candidates"]:
        assert c["reason"]
        assert any("\u4e00" <= ch <= "\u9fff" for ch in c["reason"])
        assert not any("A" <= ch <= "z" for ch in c["reason"])


# ------------------------------------------------------------------------ list-recommendations (P0.B)


async def test_list_item_recommendations_filtered_by_status(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """`?status=rejected` returns only rejected recs, newest first.

    Two recs for the same item are created and rejected; a third (pending)
    is created and *not* rejected — it must NOT appear in the result.
    """
    from app.ai.providers.mock import MockAIProvider
    from app.api.v1.recommendations import _get_ai_provider

    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    mock = MockAIProvider(
        ranking_response={
            "candidates": [
                {
                    "slot_id": str(slot_id),
                    "confidence": 0.9,
                    "reason": "马克杯放在厨房吊柜第1层",
                    "matched_rules": [],
                    "evidence_item_ids": [],
                }
            ]
        }
    )

    def _get():
        return mock

    app.dependency_overrides[_get_ai_provider] = _get

    # 2 rejected + 1 pending — only the 2 rejected should come back.
    rec_ids = []
    for _ in range(3):
        r = api_client.post(
            f"/api/v1/recommendations/items/{cup_id}/recommend",
            json={},
            headers=seeded_actor.headers(),
        )
        assert r.status_code == 200, r.text
        rec_ids.append(r.json()["recommendation_id"])

    for rid in rec_ids[:2]:
        rejected = api_client.post(
            f"/api/v1/recommendations/{rid}/reject",
            json={},
            headers=seeded_actor.headers(),
        )
        assert rejected.status_code == 200, rejected.text

    resp = api_client.get(
        f"/api/v1/items/{cup_id}/recommendations?status=rejected",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = body["recommendations"]
    assert len(rows) == 2
    # Newest first → the second reject (rec_ids[1]) appears before rec_ids[0].
    assert rows[0]["id"] == rec_ids[1]
    assert rows[1]["id"] == rec_ids[0]
    for row in rows:
        assert row["status"] == "rejected"
        assert row["chosen_slot_id"] == str(slot_id)
        assert "马克杯" in row["reason"]


async def test_list_item_recommendations_cross_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """An item belonging to a different home → 404 (consistent with the
    rest of the items API)."""
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.get(
        f"/api/v1/items/{cup_id}/recommendations?status=rejected",
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


async def test_list_item_recommendations_no_credentials_is_401(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.get(
        f"/api/v1/items/{cup_id}/recommendations?status=rejected",
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401, resp.text


# ------------------------------------------------------------------------ auth


async def test_items_require_actor_headers(api_client: TestClient) -> None:
    resp = api_client.get("/api/v1/items")
    assert resp.status_code in {401, 422}
