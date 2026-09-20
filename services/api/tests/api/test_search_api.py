"""Integration tests for ``POST /api/v1/search`` (Phase 6).

Uses the FastAPI ``TestClient`` + real in-memory SQLite. The LLM is
replaced by a scripted ``MockAIProvider`` injected via the
``_get_ai_provider`` dependency override — same pattern as the
recommendation / recognition API tests.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.search.intent import SearchIntentKind
from app.ai.providers.mock import MockAIProvider
from app.db.enums import PlacementSource
from app.main import app
from app.models import AgentTrace, Item, ItemPlacement
from tests.conftest import SeededActor
from tests.unit.conftest import StorageHierarchy

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------- helpers


def _override_provider(provider: MockAIProvider) -> None:
    """Inject a scripted provider into the search endpoint's dependency."""
    from app.api.v1 import search as search_v1

    app.dependency_overrides[search_v1._get_ai_provider] = lambda: provider


def _intent_payload(
    intent: SearchIntentKind,
    *,
    query: str = "",
    category: str = "",
    location_hint: str = "",
    clarification_needed: bool = False,
    question: str = "",
) -> dict[str, object]:
    """Build a dict the mock provider will parse into :class:`ExtractedSearchIntent`."""
    return {
        "intent": intent.value,
        "query": query,
        "category": category,
        "location_hint": location_hint,
        "clarification_needed": clarification_needed,
        "question": question,
    }


def _auth_headers(actor: SeededActor) -> dict[str, str]:
    return {
        "X-User-Id": str(actor.user_id),
        "X-Home-Id": str(actor.home_id),
    }


async def _seed_item(
    db_engine, *, home_id: uuid.UUID, user_id: uuid.UUID,
    name: str, category: str = "tool"
) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        row = Item(
            id=uuid.uuid4(),
            home_id=home_id,
            name=name,
            category=category,
            estimated_size="small",
            is_sensitive=False,
            needs_lock=False,
            created_by=user_id,
        )
        session.add(row)
        await session.commit()
        return row.id


async def _place(
    db_engine, *, item_id: uuid.UUID, slot_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ItemPlacement(
                item_id=item_id,
                slot_id=slot_id,
                source=PlacementSource.USER_MANUAL.value,
                placed_by=user_id,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------- happy paths


async def test_search_happy_path_find_item(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    item_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    await _place(
        db_engine, item_id=item_id, slot_id=slot_id, user_id=seeded_actor.user_id
    )
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="马克杯")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我的马克杯在哪里？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "answer"
    assert body["intent"] == "find_item"
    assert len(body["matches"]) == 1
    assert body["matches"][0]["name"] == "马克杯"
    assert body["matches"][0]["location"]["room_name"] == "厨房"
    assert "马克杯" in body["answer_text"]
    assert body["trace_id"] is not None
    assert body["clarification_question"] is None


async def test_search_not_found_returns_empty_matches(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="宇宙飞船")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我的宇宙飞船在哪里？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "not_found"
    assert body["matches"] == []
    assert "没有找到" in body["answer_text"]


async def test_search_multiple_matches_surface_clarification(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    await _seed_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="数据线",
    )
    await _seed_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        name="充电线",
    )
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="线")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我的线在哪？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "needs_clarification"
    names = {m["name"] for m in body["matches"]}
    assert names == {"数据线", "充电线"}
    assert body["clarification_question"] is not None
    assert "哪一个" in body["clarification_question"]


# ---------------------------------------------------------------------- error paths


async def test_search_ai_provider_error_returns_200_with_state_error(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """A provider transport failure becomes ``state='error'`` (HTTP still 200)."""
    from app.ai.errors import AIProviderTransportError

    provider = MockAIProvider(
        structured_output_responses=[AIProviderTransportError("connection reset")]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "任何查询"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "error"
    assert body["intent"] == "unknown"
    assert body["matches"] == []
    # trace_id is still persisted for debugging.
    assert body["trace_id"] is not None


async def test_search_unknown_intent_returns_clarification(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(
                SearchIntentKind.UNKNOWN,
                clarification_needed=True,
                question="请问您想找什么物品？",
            )
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "嗯"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "needs_clarification"
    assert body["clarification_question"] == "请问您想找什么物品？"


# ---------------------------------------------------------------------- validation


async def test_search_extra_field_in_request_rejected(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """``extra='forbid'`` on SearchRequest → 422."""
    response = api_client.post(
        "/api/v1/search",
        json={"query": "马克杯", "rogue": True},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 422


async def test_search_missing_query_rejected(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/api/v1/search",
        json={},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 422


async def test_search_query_too_long_rejected(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    response = api_client.post(
        "/api/v1/search",
        json={"query": "x" * 513},  # max_length=512
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------- trace persistence


async def test_search_persists_agent_trace(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """Every search call persists exactly one AgentTrace row."""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select


    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _count_traces() -> int:
        async with factory() as session:
            return (
                await session.execute(
                    select(sa_func.count()).select_from(AgentTrace)
                )
            ).scalar_one()

    before = await _count_traces()

    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="马克杯")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "马克杯在哪？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trace_id"] is not None

    after = await _count_traces()
    assert after == before + 1

    # Verify the row matches the run.
    async with factory() as session:
        row = (
            await session.execute(
                select(AgentTrace).where(AgentTrace.id == uuid.UUID(body["trace_id"]))
            )
        ).scalar_one()
        assert row.final_status == "success"
        assert row.home_id == seeded_actor.home_id
        assert row.item_id is None  # search is item-agnostic
        assert isinstance(row.steps, list) and len(row.steps) >= 1


async def test_search_cross_home_returns_not_found(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """An item the seeded actor doesn't own isn't visible to the search agent."""
    # Seed an item under a different home directly in the DB.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    other_home = uuid.uuid4()
    async with factory() as session:
        item = Item(
            id=uuid.uuid4(),
            home_id=other_home,
            name="贵重金条",
            category="tool",
            estimated_size="small",
            is_sensitive=False,
            needs_lock=False,
            created_by=uuid.uuid4(),
        )
        session.add(item)
        await session.commit()

    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="贵重金条")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "金条在哪里？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "not_found"
    assert body["matches"] == []
    # And the cross-home item is NOT in any match.
    assert "贵重金条" not in str(body["matches"])


# ---------------------------------------------------------------------- smoke: list_category


async def test_search_list_category_groups_results(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """``list_category`` intent returns the count + a room-grouped answer."""
    item_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    await _place(
        db_engine, item_id=item_id, slot_id=slot_id, user_id=seeded_actor.user_id
    )
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.LIST_CATEGORY, category="utensil")
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我家所有的厨房用品？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "answer"
    assert body["intent"] == "list_category"
    assert len(body["matches"]) == 1
    assert body["matches"][0]["name"] == "马克杯"
    assert "厨房" in body["answer_text"]
