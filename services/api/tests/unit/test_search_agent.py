"""Tests for the search agent orchestrator (Phase 6).

Uses the real in-memory SQLite DB (via the ``storage_hierarchy`` fixture)
plus a scripted ``MockAIProvider``. The agent is exercised through its
public :class:`SearchAgent` surface; we never patch internals.

The fixture's seeded items are 马克杯 + 处方药. For richer scenarios we
add a few more items (数据线, 电池, 空气炸锅) and one extra active
placement so FIND_ITEM + FIND_LOCATION have something to resolve.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.search import SearchAgent, SearchRunResult
from app.agents.search.intent import (
    ExtractedSearchIntent,
    SearchIntentKind,
)
from app.ai.providers.mock import MockAIProvider
from app.db.enums import PlacementSource
from app.models.item import Item
from app.models.placement import ItemPlacement
from app.tools.registry import get_default_registry
from tests.unit.conftest import StorageHierarchy

# ---------------------------------------------------------------------- helpers


async def _seed_extra_items(
    db_engine, seeded_actor, names: list[tuple[str, str]]
) -> dict[str, uuid.UUID]:
    """Insert additional rows under :data:`storage_hierarchy.items`.

    ``names`` is a list of ``(display_name, category)``. Returns
    ``name → item_id`` mapping.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    out: dict[str, uuid.UUID] = {}
    async with factory() as session:
        for name, category in names:
            row = Item(
                id=uuid.uuid4(),
                home_id=seeded_actor.home_id,
                name=name,
                category=category,
                estimated_size="small",
                is_sensitive=False,
                needs_lock=False,
                created_by=seeded_actor.user_id,
            )
            session.add(row)
            await session.flush()
            out[name] = row.id
        await session.commit()
    return out


async def _place_item(
    db_engine, *, home_id: uuid.UUID, user_id: uuid.UUID,
    slot_id: uuid.UUID, item_id: uuid.UUID
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


async def _agent_for(
    db_engine, *, scripted_intent: ExtractedSearchIntent
) -> tuple[SearchAgent, object]:
    """Return ``(agent, session)`` using a single session for the whole run."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    class _Holder:
        def __init__(self) -> None:
            self.sess = None

        async def __aenter__(self) -> object:
            self.sess = factory()
            return self.sess

        async def __aexit__(self, *exc: object) -> None:
            await self.sess.close()
            self.sess = None

    holder = _Holder()
    await holder.__aenter__()
    sess = holder.sess  # type: ignore[attr-defined]
    ai = MockAIProvider(
        structured_output_responses=[scripted_intent.model_dump(mode="json")]
    )
    tools = get_default_registry(sess)
    return SearchAgent(ai=ai, tools=tools, db=sess), holder


# ---------------------------------------------------------------------- FIND_ITEM


async def test_find_item_single_match_with_location(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Place the cup into L1S1, then query → answer with the kitchen path."""
    item_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=slot_id,
        item_id=item_id,
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_ITEM, query="马克杯"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我的马克杯在哪里？",
        )
        assert isinstance(result, SearchRunResult)
        assert result.state == "answer"
        assert len(result.matches) == 1
        match = result.matches[0]
        assert match["name"] == "马克杯"
        assert match["location"] is not None
        assert match["location"]["room_name"] == "厨房"
        assert "厨房" in result.answer_text
        assert "L1S1" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_find_item_not_in_db(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_ITEM, query="宇宙飞船"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我的宇宙飞船在哪？",
        )
        assert result.state == "not_found"
        assert result.matches == []
        assert "没有找到" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_find_item_not_placed_yet(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Item exists but no active placement → state='exists_but_not_placed'."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_ITEM, query="马克杯"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="马克杯在哪？",
        )
        assert result.state == "exists_but_not_placed"
        assert len(result.matches) == 1
        assert result.matches[0]["location"] is None
        assert "还没有放置" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_find_item_multiple_matches_returns_candidates(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Two distinct items sharing the substring "线" → list both, ask which."""
    await _seed_extra_items(
        db_engine,
        seeded_actor,
        [("数据线", "tool"), ("充电线", "tool")],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_ITEM, query="线"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我的线在哪？",
        )
        assert result.state == "needs_clarification"
        assert {m["name"] for m in result.matches} == {"数据线", "充电线"}
        assert "哪一个" in result.answer_text
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- FIND_ITEMS


async def test_find_items_by_query_and_location(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """room_name=厨房 + categories returns placed kitchen items."""
    item_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=slot_id,
        item_id=item_id,
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_ITEMS,
        query="马克杯",
        location_hint="厨房",
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="厨房里有什么马克杯？",
        )
        assert result.state == "answer"
        assert len(result.matches) == 1
        assert result.matches[0]["name"] == "马克杯"
        assert result.matches[0]["location"]["room_name"] == "厨房"
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- FIND_LOCATION


async def test_find_location_by_slot_label(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Query 「客厅装饰柜L1放了什么？」 → returns the item placed in L1."""
    item_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1"]  # 客厅装饰柜 → L1
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=slot_id,
        item_id=item_id,
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_LOCATION, location_hint="L1"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="客厅装饰柜L1放了什么？",
        )
        assert result.state == "answer"
        names = {m["name"] for m in result.matches}
        assert names == {"马克杯"}
        # Path should mention 客厅 / 装饰柜 / L1.
        assert "L1" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_find_location_empty_slot_returns_not_found(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """No active placement in L1 → state='not_found'."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_LOCATION, location_hint="L1"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="客厅装饰柜L1放了什么？",
        )
        assert result.state == "not_found"
        assert result.matches == []
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- CHECK_EXISTENCE


async def test_check_existence_yes(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    extras = await _seed_extra_items(
        db_engine, seeded_actor, [("电池", "tool")]
    )
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1"],
        item_id=extras["电池"],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.CHECK_EXISTENCE, query="电池"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家还有没有电池？",
        )
        assert result.state == "answer"
        assert "1 个电池" in result.answer_text
        assert result.matches[0]["location"]["room_name"] == "客厅"
    finally:
        await holder.__aexit__()


async def test_check_existence_no(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.CHECK_EXISTENCE, query="金条"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家有没有金条？",
        )
        assert result.state == "not_found"
        assert "没有" in result.answer_text
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- LIST_CATEGORY


async def test_list_category_groups_by_room(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    extras = await _seed_extra_items(
        db_engine, seeded_actor, [("数据线", "tool"), ("空气炸锅", "utensil")]
    )
    # Place 数据线 in living L1 and 空气炸锅 in kitchen L1S1.
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1"],
        item_id=extras["数据线"],
    )
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        item_id=extras["空气炸锅"],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.LIST_CATEGORY, category="tool"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家所有的工具？",
        )
        assert result.state == "answer"
        names = {m["name"] for m in result.matches}
        assert names == {"数据线"}
        assert "客厅" in result.answer_text
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- UNKNOWN


async def test_unknown_intent_returns_clarification(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Intent=UNKNOWN + clarification_needed=True → surfaces the question."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.UNKNOWN,
        clarification_needed=True,
        question="请问您想找什么物品？",
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="嗯？",
        )
        assert result.state == "needs_clarification"
        assert result.answer_text == "请问您想找什么物品？"
        assert result.intent.question == "请问您想找什么物品？"
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- error handling


async def test_db_error_during_dispatch_returns_error_state(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """A DB-side exception during dispatch becomes ``state='error'``."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    class _Holder:
        def __init__(self) -> None:
            self.sess = None

        async def __aenter__(self) -> object:
            self.sess = factory()
            return self.sess

        async def __aexit__(self, *exc: object) -> None:
            await self.sess.close()
            self.sess = None

    holder = _Holder()
    await holder.__aenter__()
    sess = holder.sess  # type: ignore[attr-defined]
    ai = MockAIProvider(
        structured_output_responses=[
            {
                "intent": "find_item",
                "query": "马克杯",
                "category": "",
                "location_hint": "",
                "clarification_needed": False,
                "question": "",
            }
        ]
    )
    tools = get_default_registry(sess)

    async def _boom(**_kwargs):
        raise RuntimeError("simulated DB outage")

    # Replace search_items with a function that raises.
    from dataclasses import replace

    broken_tools = replace(tools, search_items=_boom)
    agent = SearchAgent(ai=ai, tools=broken_tools, db=sess)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="马克杯在哪？",
        )
        assert result.state == "error"
        assert "出错" in result.answer_text
        assert result.intent.intent == SearchIntentKind.UNKNOWN
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- answer-formatter pure tests


def test_format_answer_find_item_multiple_match_order() -> None:
    """The formatters must include both names in a stable order."""
    from app.agents.search.answer import format_answer

    intent = ExtractedSearchIntent(intent=SearchIntentKind.FIND_ITEM, query="线")
    candidates = [
        {"name": "数据线", "location": None},
        {"name": "充电线", "location": None},
    ]
    state, text = format_answer(intent, candidates)
    assert state == "needs_clarification"
    assert "数据线" in text and "充电线" in text
    # The numbered list order matches input order (deterministic).
    assert text.index("数据线") < text.index("充电线")


def test_format_answer_unknown_without_question_synthesises_followup() -> None:
    """``intent=UNKNOWN`` with no ``question`` still produces a non-empty text."""
    from app.agents.search.answer import format_answer

    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.UNKNOWN, clarification_needed=True
    )
    state, text = format_answer(intent, [])
    assert state == "needs_clarification"
    assert text
