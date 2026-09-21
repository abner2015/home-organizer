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
    return actor.headers()


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


# ---------------------------------------------------------------------- suggest_placement


async def test_search_suggest_placement_returns_cta_fields(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """SUGGEST_PLACEMENT answers with a slot + reason and an empty match list."""
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(
                SearchIntentKind.SUGGEST_PLACEMENT, query="雨伞", category="decor"
            )
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我有一把雨伞适合放哪里"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "answer"
    assert body["intent"] == "suggest_placement"
    assert body["matches"] == []
    assert body["suggested_item_name"] == "雨伞"
    slot = body["suggested_slot"]
    assert slot is not None
    # The frontend parses this into a UUID before building the CTA link.
    assert uuid.UUID(slot["slot_id"])
    assert slot["full_path"]
    assert "雨伞" in body["answer_text"]


async def test_search_suggest_placement_no_candidate_still_200(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """A category no slot accepts → 200 with state='not_found', no slot."""
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(
                SearchIntentKind.SUGGEST_PLACEMENT, category="spaceship"
            )
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "飞船适合放哪里"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "not_found"
    assert body["suggested_slot"] is None
    assert body["matches"] == []


async def test_search_suggest_placement_persists_trace(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """The run is still traced, and the trace carries the new intent."""
    from sqlalchemy import select

    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(
                SearchIntentKind.SUGGEST_PLACEMENT, query="雨伞", category="decor"
            )
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我有一把雨伞适合放哪里"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trace_id"] is not None

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(select(AgentTrace))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].final_status == "success"
    assert any(
        step.get("intent") == "suggest_placement" for step in rows[0].steps
    )


# ---------------------------------------------------------------------- describe_storage


async def test_search_describe_storage_answers_with_the_hierarchy(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """「我家有几个柜子？」 — the answer must count furniture, not items.

    Regression: this request used to be routed as FIND_ITEMS with ``query="柜子"``,
    so the reply was whichever *item* happened to contain 柜子 in its name.
    """
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.DESCRIBE_STORAGE)
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我家有几个柜子？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "answer"
    assert body["intent"] == "describe_storage"
    # A structure answer has no item matches by construction.
    assert body["matches"] == []
    assert body["suggested_slot"] is None
    text = body["answer_text"]
    assert "房间 3 个" in text
    assert "收纳家具 3 件" in text
    assert "柜子 2 件" in text
    assert "抽屉柜 1 件" in text
    # Chinese labels, never the ASCII machine identity.
    assert "cabinet" not in text
    assert "L1S1" not in text


async def test_search_describe_storage_persists_a_trace(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """Still one traced turn, and the conversation memory records it."""
    from sqlalchemy import select

    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.DESCRIBE_STORAGE)
        ]
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我家一共有几个房间？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trace_id"] is not None
    assert uuid.UUID(body["conversation_id"])

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (await session.execute(select(AgentTrace))).scalars().all()
    assert len(rows) == 1
    assert rows[0].final_status == "success"
    assert any(
        step.get("intent") == "describe_storage" for step in rows[0].steps
    )


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


# ---------------------------------------------------------------------- conversation memory


async def test_first_turn_returns_a_conversation_id(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
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
    assert body["conversation_id"]
    # The turn is written down, so the next request can read it.
    from sqlalchemy import select

    from app.models import Message

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(select(Message).order_by(Message.created_at))
        ).scalars().all()
    assert [r.role for r in rows] == ["user", "assistant"]
    assert rows[0].content == "我的马克杯在哪里？"
    assert rows[1].content == body["answer_text"]


async def test_second_turn_sees_the_first_turn_in_its_prompt(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """The regression the user reported: a follow-up must not be parsed in
    isolation. Turn 2's intent prompt has to contain turn 1."""
    item_id = storage_hierarchy.items["马克杯"]
    await _place(
        db_engine,
        item_id=item_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        user_id=seeded_actor.user_id,
    )
    provider = MockAIProvider(
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="马克杯"),
            _intent_payload(SearchIntentKind.SUGGEST_PLACEMENT, query="马克杯"),
        ]
    )
    _override_provider(provider)

    first = api_client.post(
        "/api/v1/search",
        json={"query": "我的马克杯在哪里？"},
        headers=_auth_headers(seeded_actor),
    )
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]

    second = api_client.post(
        "/api/v1/search",
        json={"query": "那它放哪儿好？", "conversation_id": conversation_id},
        headers=_auth_headers(seeded_actor),
    )
    assert second.status_code == 200, second.text
    assert second.json()["conversation_id"] == conversation_id

    # The second intent call carried the first exchange.
    prompts = [
        call[1]["prompt"]
        for call in provider.recorded_calls
        if call[0] == "structured_output"
    ]
    assert len(prompts) == 2
    # Turn 1 knew no history; turn 2 did, and it included turn 1's answer.
    assert "用户：我的马克杯在哪里？" not in prompts[0]
    assert "用户：我的马克杯在哪里？" in prompts[1]
    assert "马克杯在" in prompts[1]


async def test_unknown_conversation_id_is_404(
    seeded_actor: SeededActor, api_client: TestClient
) -> None:
    _override_provider(MockAIProvider(structured_output_responses=[]))
    response = api_client.post(
        "/api/v1/search",
        json={"query": "你好", "conversation_id": str(uuid.uuid4())},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 404, response.text


async def test_composed_answer_text_is_what_the_user_sees(
    seeded_actor: SeededActor,
    db_engine,
    storage_hierarchy: StorageHierarchy,
    api_client: TestClient,
) -> None:
    """When the chat model phrases the reply, that phrasing is the response —
    and it is what gets remembered for the next turn."""
    item_id = storage_hierarchy.items["马克杯"]
    await _place(
        db_engine,
        item_id=item_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        user_id=seeded_actor.user_id,
    )
    provider = MockAIProvider(
        chat_response="你的马克杯在厨房吊柜第一层第一格～",
        structured_output_responses=[
            _intent_payload(SearchIntentKind.FIND_ITEM, query="马克杯")
        ],
    )
    _override_provider(provider)
    response = api_client.post(
        "/api/v1/search",
        json={"query": "我的马克杯在哪里？"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_text"] == "你的马克杯在厨房吊柜第一层第一格～"
    # The *structured* facts are untouched by the rewording.
    assert body["matches"][0]["location"]["full_path"] == "厨房/厨房吊柜/第1层第1格"

    from sqlalchemy import select

    from app.models import Message

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Message).where(Message.role == "assistant")
            )
        ).scalars().all()
    assert [r.content for r in rows] == ["你的马克杯在厨房吊柜第一层第一格～"]


async def test_search_request_rejects_unknown_fields(
    seeded_actor: SeededActor, api_client: TestClient
) -> None:
    """``conversation_id`` is the only new field; extra="forbid" still holds."""
    _override_provider(MockAIProvider(structured_output_responses=[]))
    response = api_client.post(
        "/api/v1/search",
        json={"query": "你好", "chat_id": "nope"},
        headers=_auth_headers(seeded_actor),
    )
    assert response.status_code == 422
