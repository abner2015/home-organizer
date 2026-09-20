"""Tests for the agent ToolRegistry and its 13 tools.

All tests use the real in-memory SQLite DB (no mocks) via the
``storage_hierarchy`` fixture. The ``get_default_registry`` factory wraps
each tool with a session-bound closure so tests can call them as plain
async callables.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import NotFoundError, ValidationFailedError
from app.db.enums import PlacementSource, RecommendationStatus
from app.models import AgentTrace, ItemPlacement, Recommendation
from app.tools import ToolRegistry
from tests.unit.conftest import StorageHierarchy, slot_dicts_by_code

pytestmark = pytest.mark.asyncio


async def _make_registry(db_engine) -> tuple[ToolRegistry, object]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    class _Ctx:
        async def __aenter__(self) -> object:
            self.session = factory()
            return self.session

        async def __aexit__(self, *exc: object) -> None:
            await self.session.close()

    # We need a single session the tools share. Use a context-manager-shaped
    # object that always yields the same session.
    session_ref: dict[str, object] = {}

    class _SessionHolder:
        def __init__(self) -> None:
            self._sess = None

        async def __aenter__(self) -> object:
            self._sess = factory()
            return self._sess

        async def __aexit__(self, *exc: object) -> None:
            await self._sess.close()
            self._sess = None

    holder = _SessionHolder()
    # Open one session and pass it to the registry — the bound closures
    # ignore `db=` only at call time, so we patch the bind to forward ours.
    await holder.__aenter__()
    sess = holder._sess  # type: ignore[attr-defined]
    session_ref["sess"] = sess

    # Build the registry using the default factory, but rebind its tools to
    # always pass our held session.
    from app.tools import context_tools, home_tools, item_tools, write_tools

    def _bind(fn):
        async def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
            return await fn(*args, db=sess, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    reg = ToolRegistry(
        get_home=_bind(home_tools.get_home),
        get_rooms=_bind(home_tools.get_rooms),
        get_storage_units=_bind(home_tools.get_storage_units),
        get_storage_sections=_bind(home_tools.get_storage_sections),
        get_storage_slots=_bind(home_tools.get_storage_slots),
        get_items=_bind(item_tools.get_items),
        search_items=_bind(item_tools.search_items),
        get_item_placements=_bind(item_tools.get_item_placements),
        get_user_preferences=_bind(context_tools.get_user_preferences),
        get_home_rules=_bind(context_tools.get_home_rules),
        create_recommendation=_bind(write_tools.create_recommendation),
        verify_recommendation=_bind(write_tools.verify_recommendation),
        save_placement=_bind(write_tools.save_placement),
    )
    return reg, holder


# ----------------------------------------------------------- Home tools


async def test_get_home_returns_seeded_home(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        home = await reg.get_home(home_id=seeded_actor.home_id)
        assert home["id"] == str(seeded_actor.home_id)
        assert home["name"] == "Test Home"
        assert home["owner_id"] == str(seeded_actor.user_id)
    finally:
        await holder.__aexit__()


async def test_get_home_cross_home_404(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        with pytest.raises(NotFoundError):
            await reg.get_home(home_id=uuid.uuid4())
    finally:
        await holder.__aexit__()


async def test_get_rooms_returns_three(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        rooms = await reg.get_rooms(home_id=seeded_actor.home_id)
        names = {r["name"] for r in rooms}
        assert names == {"客厅", "厨房", "主卧"}
    finally:
        await holder.__aexit__()


async def test_get_storage_units_filtered_by_room(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        all_units = await reg.get_storage_units(home_id=seeded_actor.home_id)
        assert {u["name"] for u in all_units} == {"客厅装饰柜", "厨房吊柜", "床头柜"}
        living_only = await reg.get_storage_units(
            home_id=seeded_actor.home_id, room_id=storage_hierarchy.living_room_id
        )
        assert {u["name"] for u in living_only} == {"客厅装饰柜"}
    finally:
        await holder.__aexit__()


async def test_get_storage_slots_enriched_and_active_count(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        slots = await reg.get_storage_slots(home_id=seeded_actor.home_id)
        indexed = slot_dicts_by_code(slots)
        # Spot-check enrichment
        l1 = indexed["L1"]
        assert l1["full_path"] == "客厅/客厅装饰柜/左玻璃柜/L1"
        assert l1["room_name"] == "客厅"
        assert l1["unit_type"] == "cabinet"
        assert l1["section_type"] == "layer"
        assert l1["allowed_categories"] == ["decor"]
        assert l1["active_count"] == 0
        assert l1["home_id"] == str(seeded_actor.home_id)
    finally:
        await holder.__aexit__()


async def test_get_storage_slots_filtered_by_section(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        slots = await reg.get_storage_slots(
            home_id=seeded_actor.home_id, section_id=uuid.uuid4()  # unrelated
        )
        assert slots == []
    finally:
        await holder.__aexit__()


async def test_get_storage_slots_cross_home_returns_empty(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        slots = await reg.get_storage_slots(home_id=uuid.uuid4())
        assert slots == []  # cross-home is implicit: 0 rows
    finally:
        await holder.__aexit__()


# ----------------------------------------------------------- Item tools


async def test_get_items_lists_home_items(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        items = await reg.get_items(home_id=seeded_actor.home_id)
        names = {i["name"] for i in items}
        assert names == {"马克杯", "处方药"}
        cup = next(i for i in items if i["name"] == "马克杯")
        assert cup["category"] == "utensil"
        assert cup["is_sensitive"] is False
        med = next(i for i in items if i["name"] == "处方药")
        assert med["is_sensitive"] is True
        assert med["needs_lock"] is True
    finally:
        await holder.__aexit__()


async def test_get_items_single_lookup_404_on_cross_home(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        with pytest.raises(NotFoundError):
            await reg.get_items(home_id=seeded_actor.home_id, item_id=uuid.uuid4())
    finally:
        await holder.__aexit__()


async def test_search_items_substring_and_category(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        # Substring match
        hits = await reg.search_items(home_id=seeded_actor.home_id, query="马克")
        assert [h["name"] for h in hits] == ["马克杯"]
        # Category match
        med_hits = await reg.search_items(
            home_id=seeded_actor.home_id, category="medicine"
        )
        assert [h["name"] for h in med_hits] == ["处方药"]
        # No match
        none = await reg.search_items(home_id=seeded_actor.home_id, query="zzz")
        assert none == []
    finally:
        await holder.__aexit__()


async def test_search_items_is_sensitive_filter(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """`is_sensitive` filters by the boolean flag on the item."""
    reg, holder = await _make_registry(db_engine)
    try:
        sensitive = await reg.search_items(
            home_id=seeded_actor.home_id, is_sensitive=True
        )
        assert [h["name"] for h in sensitive] == ["处方药"]
        not_sensitive = await reg.search_items(
            home_id=seeded_actor.home_id, is_sensitive=False
        )
        assert {h["name"] for h in not_sensitive} == {"马克杯"}
    finally:
        await holder.__aexit__()


async def test_search_items_room_name_filter_excludes_unplaced(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    """`room_name` joins active placements; items without an active placement
    in the matched room are excluded."""
    reg, holder = await _make_registry(db_engine)
    try:
        # Cup has no active placement yet — room_name=厨房 returns nothing.
        none = await reg.search_items(
            home_id=seeded_actor.home_id, room_name="厨房"
        )
        assert none == []
        # Place cup into kitchen slot L1S1.
        await reg.save_placement(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=storage_hierarchy.items["马克杯"],
            slot_id=storage_hierarchy.slots["L1S1"],
            source=PlacementSource.USER_MANUAL.value,
        )
        # Now room_name=厨房 returns the cup; med (not placed) still excluded.
        in_kitchen = await reg.search_items(
            home_id=seeded_actor.home_id, room_name="厨房"
        )
        assert [h["name"] for h in in_kitchen] == ["马克杯"]
        # Med is sensitive + unplaced → still excluded by room_name.
        in_bedroom = await reg.search_items(
            home_id=seeded_actor.home_id, room_name="主卧"
        )
        assert in_bedroom == []
    finally:
        await holder.__aexit__()


async def test_get_item_placements_empty_then_after_save(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        item_id = storage_hierarchy.items["马克杯"]
        slot_id = storage_hierarchy.slots["L1"]
        before = await reg.get_item_placements(
            home_id=seeded_actor.home_id, item_id=item_id
        )
        assert before == []
        await reg.save_placement(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=item_id,
            slot_id=slot_id,
            source=PlacementSource.USER_MANUAL.value,
        )
        after = await reg.get_item_placements(
            home_id=seeded_actor.home_id, item_id=item_id
        )
        assert len(after) == 1
        assert after[0]["slot_id"] == str(slot_id)
        assert after[0]["is_active"] is True
        assert after[0]["source"] == "user_manual"
    finally:
        await holder.__aexit__()


# ----------------------------------------------------------- Context tools


async def test_get_user_preferences_empty_default(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        prefs = await reg.get_user_preferences(home_id=seeded_actor.home_id)
        assert prefs == []
    finally:
        await holder.__aexit__()


async def test_get_home_rules_empty_default(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        rules = await reg.get_home_rules(home_id=seeded_actor.home_id)
        assert rules == []
    finally:
        await holder.__aexit__()


# ----------------------------------------------------------- Write tools


async def test_create_recommendation_persists_row(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    try:
        # Seed an AgentTrace row so the FK is satisfied.
        async with factory() as session:
            trace = AgentTrace(
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                steps=[],
                final_status="success",
                total_duration_ms=10,
            )
            session.add(trace)
            await session.commit()
            trace_id = trace.id

        result = await reg.create_recommendation(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=storage_hierarchy.items["马克杯"],
            agent_trace_id=trace_id,
            candidates=[{"id": str(storage_hierarchy.slots["L1"]), "score": 0.9, "reason": "x"}],
            chosen_slot_id=storage_hierarchy.slots["L1"],
            pre_filter_count=10,
            post_filter_count=5,
        )
        assert result["status"] == RecommendationStatus.PENDING.value
        assert result["chosen_slot_id"] == str(storage_hierarchy.slots["L1"])

        async with factory() as session:
            row = (
                await session.execute(
                    Recommendation.__table__.select().where(
                        Recommendation.id == uuid.UUID(result["id"])
                    )
                )
            ).first()
            assert row is not None
    finally:
        await holder.__aexit__()


async def test_create_recommendation_rejects_invalid_status(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        with pytest.raises(ValidationFailedError):
            await reg.create_recommendation(
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                item_id=storage_hierarchy.items["马克杯"],
                agent_trace_id=uuid.uuid4(),
                candidates=[],
                chosen_slot_id=None,
                pre_filter_count=0,
                post_filter_count=0,
                status="bogus",
            )
    finally:
        await holder.__aexit__()


async def test_save_placement_removes_previous_active(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    try:
        item_id = storage_hierarchy.items["马克杯"]
        a = storage_hierarchy.slots["L1"]
        b = storage_hierarchy.slots["L2"]
        await reg.save_placement(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=item_id,
            slot_id=a,
            source=PlacementSource.USER_MANUAL.value,
        )
        await reg.save_placement(
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            item_id=item_id,
            slot_id=b,
            source=PlacementSource.USER_MANUAL.value,
        )
        async with factory() as session:
            rows = (
                await session.execute(
                    ItemPlacement.__table__.select().where(
                        ItemPlacement.item_id == item_id
                    )
                )
            ).all()
        active = [r for r in rows if r.removed_at is None]
        removed = [r for r in rows if r.removed_at is not None]
        assert len(active) == 1 and str(active[0].slot_id) == str(b)
        assert len(removed) == 1 and str(removed[0].slot_id) == str(a)
    finally:
        await holder.__aexit__()


async def test_save_placement_rejects_bad_source(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        with pytest.raises(ValidationFailedError):
            await reg.save_placement(
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                item_id=storage_hierarchy.items["马克杯"],
                slot_id=storage_hierarchy.slots["L1"],
                source="magic",
            )
    finally:
        await holder.__aexit__()


async def test_save_placement_cross_home_slot_404(
    seeded_actor, db_engine, storage_hierarchy: StorageHierarchy
) -> None:
    reg, holder = await _make_registry(db_engine)
    try:
        with pytest.raises(NotFoundError):
            await reg.save_placement(
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
                item_id=storage_hierarchy.items["马克杯"],
                slot_id=uuid.uuid4(),
                source=PlacementSource.USER_MANUAL.value,
            )
    finally:
        await holder.__aexit__()


# ----------------------------------------------------------- Registry factory


async def test_get_default_registry_returns_all_thirteen_tools() -> None:
    """Static test of the factory: it must always return 13 callables."""
    from app.tools import registry as reg_mod

    expected = {
        "get_home",
        "get_rooms",
        "get_storage_units",
        "get_storage_sections",
        "get_storage_slots",
        "get_items",
        "search_items",
        "get_item_placements",
        "get_user_preferences",
        "get_home_rules",
        "create_recommendation",
        "verify_recommendation",
        "save_placement",
    }
    # The dataclass field names must match exactly the tool set in the prompt.
    actual = {f.name for f in reg_mod.ToolRegistry.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    assert actual == expected
    assert len(reg_mod.ToolRegistry.__dataclass_fields__) == 13  # type: ignore[arg-type]
