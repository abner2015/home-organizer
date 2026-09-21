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
        assert "第1层第1格" in result.answer_text
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
        # Path is human-readable: room / unit / slot label, no ASCII code.
        assert "客厅/客厅装饰柜/左玻璃柜第1层" in result.answer_text
        assert "L1" not in result.answer_text
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


async def test_find_location_room_plus_code_excludes_other_rooms_same_prefix(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Bug-2 regression: hint 「客厅装饰柜 L1」 must ignore the kitchen's L1S1.

    Before the tokenizer, ``hint in full_path`` failed on the space/slash mix,
    so the assistant claimed 「客厅装饰柜 L1里没有放置任何物品」 for a slot that
    really held 马克杯. The kitchen's L1S1 (same code prefix, different room)
    must stay excluded by the name fragment.
    """
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1"],
        item_id=storage_hierarchy.items["马克杯"],
    )
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        item_id=storage_hierarchy.items["处方药"],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_LOCATION, location_hint="客厅装饰柜 L1"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="客厅装饰柜 L1 放了什么？",
        )
        assert result.state == "answer"
        assert {m["name"] for m in result.matches} == {"马克杯"}
    finally:
        await holder.__aexit__()


async def test_find_location_unit_name_returns_every_slot_under_it(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Naming the whole cabinet returns items from all of its slots."""
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L2"],
        item_id=storage_hierarchy.items["马克杯"],
    )
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["R1"],
        item_id=storage_hierarchy.items["处方药"],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_LOCATION, location_hint="客厅装饰柜"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="客厅装饰柜放了什么？",
        )
        assert result.state == "answer"
        assert {m["name"] for m in result.matches} == {"马克杯", "处方药"}
    finally:
        await holder.__aexit__()


async def test_find_location_bare_code_still_reaches_nested_slots(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """A bare 「L1」 keeps the old prefix recall — the kitchen L1S1 stays reachable."""
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1S1"],
        item_id=storage_hierarchy.items["马克杯"],
    )
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.FIND_LOCATION, location_hint="L1"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="L1放了什么？",
        )
        assert result.state == "answer"
        assert {m["name"] for m in result.matches} == {"马克杯"}
    finally:
        await holder.__aexit__()


# ---------------------------------------------------------------------- SUGGEST_PLACEMENT


async def test_suggest_placement_known_category_returns_slot(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """「雨伞放哪里」 → answer state + a suggested slot, but zero matches."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.SUGGEST_PLACEMENT, query="雨伞", category="decor"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我有一把雨伞适合放哪里",
        )
        assert result.state == "answer"
        assert result.matches == []
        assert result.suggested_slot is not None
        assert result.suggested_slot["full_path"]
        assert result.suggested_item_name == "雨伞"
        assert "雨伞" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_suggest_placement_without_category_still_answers(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """With no category the gate is permissive, so a suggestion still comes back."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.SUGGEST_PLACEMENT, query="手工纪念册"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="手工纪念册放哪好？",
        )
        assert result.state == "answer"
        assert result.suggested_slot is not None
    finally:
        await holder.__aexit__()


async def test_suggest_placement_unknown_category_returns_not_found(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """A category no slot allows yields no candidate → not_found, no slot."""
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.SUGGEST_PLACEMENT, category="spaceship"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="飞船适合放哪里",
        )
        assert result.state == "not_found"
        assert result.suggested_slot is None
        assert "没找到" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_suggest_placement_without_item_asks_for_clarification(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """No name and no category → ask what to store instead of guessing."""
    intent = ExtractedSearchIntent(intent=SearchIntentKind.SUGGEST_PLACEMENT)
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="这个东西放哪里？",
        )
        assert result.state == "needs_clarification"
        assert result.suggested_slot is None
        assert result.answer_text
    finally:
        await holder.__aexit__()


async def test_suggest_placement_writes_nothing(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Read-only guarantee: no ItemPlacement / Recommendation rows are created."""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from app.models import Recommendation

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _counts() -> tuple[int, int]:
        async with factory() as session:
            placements = (
                await session.execute(
                    select(sa_func.count()).select_from(ItemPlacement)
                )
            ).scalar_one()
            recs = (
                await session.execute(
                    select(sa_func.count()).select_from(Recommendation)
                )
            ).scalar_one()
            return placements, recs

    before = await _counts()
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.SUGGEST_PLACEMENT, query="雨伞", category="decor"
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我有一把雨伞适合放哪里",
        )
        assert result.suggested_slot is not None
    finally:
        await holder.__aexit__()
    assert await _counts() == before


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


# ---------------------------------------------------------------------- DESCRIBE_STORAGE


async def test_describe_storage_counts_the_real_hierarchy(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """「我家有几个柜子？」 — the fixture holds 3 rooms and 3 units.

    Regression: this used to be routed as FIND_ITEMS with ``query="柜子"``, so
    the answer was whichever *item* had 柜子 in its name. Nothing about the
    storage furniture was ever read.
    """
    intent = ExtractedSearchIntent(
        intent=SearchIntentKind.DESCRIBE_STORAGE, location_hint=""
    )
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家有几个柜子？",
        )
        assert result.state == "answer"
        # No items are involved in a structure answer.
        assert result.matches == []
        assert result.intent.intent == SearchIntentKind.DESCRIBE_STORAGE
        assert "房间 3 个" in result.answer_text
        assert "收纳家具 3 件" in result.answer_text
        assert "柜子 2 件" in result.answer_text
        assert "抽屉柜 1 件" in result.answer_text
        for name in ("客厅", "厨房", "主卧", "客厅装饰柜", "厨房吊柜", "床头柜"):
            assert name in result.answer_text
    finally:
        await holder.__aexit__()


async def test_describe_storage_reports_occupancy(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """「收纳空间够不够用？」 needs the used/free split, not just the total."""
    await _place_item(
        db_engine,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        slot_id=storage_hierarchy.slots["L1"],
        item_id=storage_hierarchy.items["马克杯"],
    )
    intent = ExtractedSearchIntent(intent=SearchIntentKind.DESCRIBE_STORAGE)
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家收纳空间够用吗？",
        )
        # 11 positions across the fixture's 6 sections, one now occupied.
        assert "收纳位 11 个" in result.answer_text
        assert "已放入物品 1 件" in result.answer_text
        assert "空余 10 个" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_describe_storage_uses_chinese_labels_only(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Slot `code`/`unit_type` are ASCII identity — never user-facing text."""
    intent = ExtractedSearchIntent(intent=SearchIntentKind.DESCRIBE_STORAGE)
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家有哪些柜子？",
        )
        assert "cabinet" not in result.answer_text
        assert "drawer_cabinet" not in result.answer_text
        # `L1` is 左玻璃柜第1层's code; the label must be shown instead.
        assert "左玻璃柜第1层" in result.answer_text
        assert "L1S1" not in result.answer_text
    finally:
        await holder.__aexit__()


async def test_describe_storage_on_a_home_without_rooms_is_not_found(
    seeded_actor, db_engine
) -> None:
    """No hierarchy at all is a real answer ("nothing recorded yet"), not an error."""
    intent = ExtractedSearchIntent(intent=SearchIntentKind.DESCRIBE_STORAGE)
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        result = await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家有几个柜子？",
        )
        assert result.state == "not_found"
        assert "还没有录入任何房间和收纳空间" in result.answer_text
    finally:
        await holder.__aexit__()


async def test_describe_storage_writes_nothing(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """Structure questions are read-only like every other search intent."""
    intent = ExtractedSearchIntent(intent=SearchIntentKind.DESCRIBE_STORAGE)
    agent, holder = await _agent_for(db_engine, scripted_intent=intent)
    try:
        await agent.run(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            query="我家有几个柜子？",
        )

        from sqlalchemy import select

        async def _count(model: type) -> int:
            factory = async_sessionmaker(db_engine, expire_on_commit=False)
            async with factory() as s:
                return len((await s.execute(select(model))).scalars().all())

        from app.models.placement import ItemPlacement
        from app.models.recommendation import Recommendation

        assert await _count(Recommendation) == 0
        assert await _count(ItemPlacement) == 0
    finally:
        await holder.__aexit__()


def test_format_describe_storage_empty_blueprint_is_not_found() -> None:
    from app.agents.search.answer import format_describe_storage

    state, text = format_describe_storage(
        ExtractedSearchIntent(intent=SearchIntentKind.DESCRIBE_STORAGE), "   "
    )
    assert state == "not_found"
    assert text


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
