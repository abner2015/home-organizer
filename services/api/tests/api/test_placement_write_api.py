"""Integration tests for the manual placement API (P0.3 "反向录入").

Covers ``POST /placements`` and ``DELETE /placements/{id}``:

- Happy paths: item → slot, then soft-close; the item's ``current_placement``
  and the slot's ``active_count`` follow.
- The single-active-placement invariant: placing twice leaves exactly one
  active row and closes the first. Asserted by **counting rows** — SQLite does
  not enforce the PG partial unique index, so an index-backed database would
  hide a regression here.
- No LLM on the path: ``agent_traces`` row count must not move.
- Cross-home item / slot / placement → 404 (never 403); missing credentials → 401.
- Request validation: unknown fields and a missing ``slot_id`` → 422.

Auth uses real signed JWTs via ``seeded_actor.headers()``, not
``dependency_overrides`` — overriding would skip the auth path entirely.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.enums import StorageSectionType, StorageUnitType
from app.models import AgentTrace, ItemPlacement
from app.models.room import Room
from app.models.storage import StorageSection, StorageSlot, StorageUnit

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------- helpers


def _session(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _count_active_placements(db_engine, item_id: uuid.UUID) -> list[ItemPlacement]:
    """Every active placement for the item — the raw rows, not a dict.

    Callers assert ``len(...) == 1``: a dict-keyed lookup would silently hide
    the SQLite double-active bug this suite exists to catch.
    """
    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.item_id == item_id,
                    ItemPlacement.removed_at.is_(None),
                )
            )
        ).scalars().all()
        return list(rows)


async def _count_traces(db_engine) -> int:
    async with _session(db_engine)() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(AgentTrace)
                )
            ).scalar_one()
        )


def _slot_active_count(tree: dict, slot_id: uuid.UUID) -> int:
    for room in tree["rooms"]:
        for unit in room.get("units", []):
            for section in unit.get("sections", []):
                for slot in section.get("slots", []):
                    if uuid.UUID(slot["id"]) == slot_id:
                        return int(slot["active_count"])
    raise AssertionError(f"slot {slot_id} missing from the space tree")


async def _make_foreign_slot(db_engine) -> uuid.UUID:
    """A slot in a home the seeded actor is not a member of."""
    async with _session(db_engine)() as session:
        room = Room(
            home_id=uuid.uuid4(), name="Garage", room_type="other", sort_order=1
        )
        session.add(room)
        await session.flush()
        unit = StorageUnit(
            room_id=room.id,
            name="Foreign",
            unit_type=StorageUnitType.CABINET.value,
            sort_order=1,
        )
        session.add(unit)
        await session.flush()
        section = StorageSection(
            unit_id=unit.id,
            name="S1",
            section_type=StorageSectionType.LAYER.value,
            sort_order=1,
        )
        session.add(section)
        await session.flush()
        slot = StorageSlot(
            section_id=section.id,
            code="X1",
            label="X",
            allowed_categories=["misc"],
            sort_order=1,
        )
        session.add(slot)
        await session.commit()
        return slot.id


def _place(
    api_client: TestClient, actor, item_id: uuid.UUID, slot_id: uuid.UUID
):
    return api_client.post(
        "/api/v1/placements",
        json={"item_id": str(item_id), "slot_id": str(slot_id)},
        headers=actor.headers(),
    )


# ------------------------------------------------------------------------- place


async def test_place_item_happy_path(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]

    resp = _place(api_client, seeded_actor, cup_id, slot_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["item_id"] == str(cup_id)
    assert body["slot_id"] == str(slot_id)
    assert body["source"] == "user_manual"
    assert body["removed_at"] is None
    assert body["slot_path"], "the response must carry a display path"

    active = await _count_active_placements(db_engine, cup_id)
    assert len(active) == 1
    assert active[0].slot_id == slot_id
    assert active[0].recommendation_id is None

    # The item view the Web app already renders must reflect the new location.
    item = api_client.get(
        f"/api/v1/items/{cup_id}", headers=seeded_actor.headers()
    )
    assert item.status_code == 200
    assert item.json()["current_placement"]["slot_id"] == str(slot_id)


async def test_place_item_has_an_empty_reason(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """A manual placement has nothing to explain; the item page's
    「为什么放这里」 stays hidden (P0.4)."""
    resp = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["reason"] == ""


async def test_place_item_makes_no_llm_call(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """The whole point of 反向录入: recording a known location costs no tokens."""
    before = await _count_traces(db_engine)

    resp = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    assert resp.status_code == 201, resp.text

    assert await _count_traces(db_engine) == before


async def test_place_twice_keeps_exactly_one_active(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """Moving an item closes the previous placement — explicitly, by row count.

    SQLite never enforced ``uq_item_placements_one_active_per_item``, so this
    assertion is the only thing standing between a regression and two active
    rows silently coexisting.
    """
    cup_id = storage_hierarchy.items["马克杯"]
    first_slot = storage_hierarchy.slots["L1S1"]
    second_slot = storage_hierarchy.slots["L1S2"]

    assert _place(api_client, seeded_actor, cup_id, first_slot).status_code == 201
    resp = _place(api_client, seeded_actor, cup_id, second_slot)
    assert resp.status_code == 201, resp.text

    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()

    assert len(rows) == 2, "the old row must be kept, not deleted"
    active = [r for r in rows if r.removed_at is None]
    closed = [r for r in rows if r.removed_at is not None]
    assert len(active) == 1
    assert active[0].slot_id == second_slot
    assert len(closed) == 1
    assert closed[0].slot_id == first_slot
    # The close timestamp is written by the same aware `utc_now` helper the
    # `placed_at` default uses — pinned in tests/unit/test_placement_service.py,
    # since SQLite drops the offset on read and can't observe it here.
    assert closed[0].removed_at >= closed[0].placed_at


async def test_place_item_cross_home_item_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = _place(
        api_client,
        seeded_actor,
        uuid.uuid4(),
        storage_hierarchy.slots["L1S1"],
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_place_item_cross_home_slot_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    foreign_slot = await _make_foreign_slot(db_engine)
    resp = _place(
        api_client, seeded_actor, storage_hierarchy.items["马克杯"], foreign_slot
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


async def test_place_item_unknown_slot_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = _place(
        api_client, seeded_actor, storage_hierarchy.items["马克杯"], uuid.uuid4()
    )
    assert resp.status_code == 404


async def test_place_item_without_credentials_is_401(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.post(
        "/api/v1/placements",
        json={
            "item_id": str(storage_hierarchy.items["马克杯"]),
            "slot_id": str(storage_hierarchy.slots["L1S1"]),
        },
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


async def test_place_item_rejects_extra_fields(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.post(
        "/api/v1/placements",
        json={
            "item_id": str(storage_hierarchy.items["马克杯"]),
            "slot_id": str(storage_hierarchy.slots["L1S1"]),
            "hacker": True,
        },
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


async def test_place_item_requires_slot_id(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.post(
        "/api/v1/placements",
        json={"item_id": str(storage_hierarchy.items["马克杯"])},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


# ----------------------------------------------------------------------- unplace


async def test_unplace_item_soft_closes(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    placed = _place(api_client, seeded_actor, cup_id, slot_id)
    assert placed.status_code == 201, placed.text
    placement_id = placed.json()["id"]

    resp = api_client.delete(
        f"/api/v1/placements/{placement_id}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == placement_id
    assert body["removed_at"] is not None

    # Soft close: the row survives, the item is simply nowhere.
    assert await _count_active_placements(db_engine, cup_id) == []
    history = api_client.get(
        f"/api/v1/items/{cup_id}/placements", headers=seeded_actor.headers()
    )
    assert history.status_code == 200
    assert len(history.json()) == 1

    item = api_client.get(f"/api/v1/items/{cup_id}", headers=seeded_actor.headers())
    assert item.json()["current_placement"] is None


async def test_unplace_twice_is_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]

    first = api_client.delete(
        f"/api/v1/placements/{placement_id}", headers=seeded_actor.headers()
    )
    assert first.status_code == 200, first.text
    second = api_client.delete(
        f"/api/v1/placements/{placement_id}", headers=seeded_actor.headers()
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "conflict"


async def test_unplace_cross_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]

    resp = api_client.delete(
        f"/api/v1/placements/{placement_id}",
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_unplace_unknown_id_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.delete(
        f"/api/v1/placements/{uuid.uuid4()}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


async def test_unplace_without_credentials_is_401(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    resp = api_client.delete(
        f"/api/v1/placements/{placed.json()['id']}",
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


# ------------------------------------------------------------------ space tree


async def test_space_tree_active_count_tracks_placement(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """The room → unit → section → slot tree is how the user sees occupancy."""
    slot_id = storage_hierarchy.slots["L1S1"]
    cup_id = storage_hierarchy.items["马克杯"]
    url = f"/api/v1/homes/{seeded_actor.home_id}/space-tree"

    before = api_client.get(url, headers=seeded_actor.headers())
    assert before.status_code == 200, before.text
    assert _slot_active_count(before.json(), slot_id) == 0

    placed = _place(api_client, seeded_actor, cup_id, slot_id)
    assert placed.status_code == 201, placed.text

    after = api_client.get(url, headers=seeded_actor.headers())
    assert _slot_active_count(after.json(), slot_id) == 1

    removed = api_client.delete(
        f"/api/v1/placements/{placed.json()['id']}",
        headers=seeded_actor.headers(),
    )
    assert removed.status_code == 200, removed.text

    final = api_client.get(url, headers=seeded_actor.headers())
    assert _slot_active_count(final.json(), slot_id) == 0


async def test_place_item_leaves_pending_recommendation_alone(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A manual placement must not "resolve" an AI suggestion.

    The item may still be recommended afterwards, and accepting that
    recommendation closes the manual placement (see the service tests).
    """
    from app.models import Recommendation

    cup_id = storage_hierarchy.items["马克杯"]
    rec = Recommendation(
        item_id=cup_id,
        agent_trace_id=uuid.uuid4(),
        candidates=[],
        chosen_slot_id=storage_hierarchy.slots["L1S1"],
        pre_filter_count=1,
        post_filter_count=1,
        status="pending",
    )
    async with _session(db_engine)() as session:
        session.add(rec)
        await session.commit()

    resp = _place(
        api_client, seeded_actor, cup_id, storage_hierarchy.slots["L1S2"]
    )
    assert resp.status_code == 201, resp.text

    async with _session(db_engine)() as session:
        reloaded = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == rec.id)
            )
        ).scalar_one()
        assert reloaded.status == "pending"


async def test_place_item_foreign_slot_leaves_item_without_placement(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A rejected request must not half-apply: no placement row at all."""
    foreign_slot = await _make_foreign_slot(db_engine)
    resp = _place(
        api_client, seeded_actor, storage_hierarchy.items["马克杯"], foreign_slot
    )
    assert resp.status_code == 404

    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.item_id == storage_hierarchy.items["马克杯"]
                )
            )
        ).scalars().all()
    assert rows == []

    item = api_client.get(
        f"/api/v1/items/{storage_hierarchy.items['马克杯']}",
        headers=seeded_actor.headers(),
    ).json()
    assert item["current_placement"] is None


async def test_concurrent_place_returns_409(
    api_client: TestClient,
    seeded_actor,
    storage_hierarchy,
    db_engine,
) -> None:
    """The partial unique index ``uq_item_placements_one_active_per_item``
    makes "one item, one active slot" a DB invariant. Two concurrent
    POST /placements for the same item both run the close-then-insert
    sequence; the loser's flush collides with the winner's already-committed
    row and PG raises IntegrityError. The endpoint must translate that to
    409 (not 500) so the caller knows it's a race they can retry.

    SQLite doesn't enforce the index, so we simulate the race by monkeypatching
    the session's flush to raise the same IntegrityError. The production
    path is identical after the patch — _create_placement catches
    IntegrityError on flush, rolls back, and raises ConflictError.
    """
    from unittest import mock

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]

    async def _failing_flush(_self):
        # Mirrors what PG would say when the loser of the race hits the
        # partial unique index. The exact message text is not load-bearing —
        # the writer's _constraint_name_from_integrity_error inspects the
        # message only as a last resort, after the structured diag object.
        raise IntegrityError(
            "duplicate key value violates unique constraint "
            '"uq_item_placements_one_active_per_item"',
            params=None,
            orig=Exception(
                "duplicate key value violates unique constraint "
                '"uq_item_placements_one_active_per_item"'
            ),
        )

    with mock.patch.object(AsyncSession, "flush", new=_failing_flush):
        resp = _place(api_client, seeded_actor, cup_id, slot_id)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "conflict"
    assert "already being placed" in body["error"]["message"]
    assert body["error"]["details"]["constraint"] == (
        "uq_item_placements_one_active_per_item"
    )

    # No row should have been committed — _create_placement rolled back the
    # session before raising, and the route's commit was never reached.
    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
    assert rows == [], "a 409 placement must not leave any row behind"


# ----------------------------------------------------------------------- patch
# PATCH /placements/{id} — edit a placement's note and/or move it to a
# different slot (P0.7).


def _patch(
    api_client: TestClient,
    actor,
    placement_id: uuid.UUID,
    body: dict,
):
    return api_client.patch(
        f"/api/v1/placements/{placement_id}",
        json=body,
        headers=actor.headers(),
    )


async def test_patch_placement_edits_note(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """PATCH `{"note": "..."}` → 200, DB row note updated, slot unchanged."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    placed = _place(api_client, seeded_actor, cup_id, slot_id)
    assert placed.status_code == 201, placed.text
    placement_id = placed.json()["id"]

    resp = _patch(api_client, seeded_actor, placement_id, {"note": "新加备注"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == placement_id
    assert body["note"] == "新加备注"
    assert body["slot_id"] == str(slot_id)

    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.id == uuid.UUID(placement_id))
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].note == "新加备注"
    assert rows[0].slot_id == slot_id


async def test_patch_placement_clears_note(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """`{"note": null}` → 200, DB row note cleared."""
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]

    # Set a note first, then clear it.
    assert _patch(api_client, seeded_actor, placement_id, {"note": "x"}).status_code == 200
    resp = _patch(api_client, seeded_actor, placement_id, {"note": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] is None

    async with _session(db_engine)() as session:
        row = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.id == uuid.UUID(placement_id))
            )
        ).scalar_one()
        assert row.note is None


async def test_patch_placement_moves_slot(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """`{"slot_id": new}` → 200, old row closed, new row active."""
    cup_id = storage_hierarchy.items["马克杯"]
    first = storage_hierarchy.slots["L1S1"]
    second = storage_hierarchy.slots["L1S2"]
    placed = _place(api_client, seeded_actor, cup_id, first)
    old_id = placed.json()["id"]

    resp = _patch(api_client, seeded_actor, old_id, {"slot_id": str(second)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot_id"] == str(second)
    new_id = body["id"]
    assert new_id != old_id

    # Exactly one active, one closed.
    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
    active = [r for r in rows if r.removed_at is None]
    closed = [r for r in rows if r.removed_at is not None]
    assert len(active) == 1 and active[0].id == uuid.UUID(new_id)
    assert len(closed) == 1 and closed[0].id == uuid.UUID(old_id)


async def test_patch_placement_move_inherits_note(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """Only `slot_id` provided → new row's note = old row's note."""
    cup_id = storage_hierarchy.items["马克杯"]
    first = storage_hierarchy.slots["L1S1"]
    second = storage_hierarchy.slots["L1S2"]
    # Create a placement with a note via the manual endpoint. POST /placements
    # accepts note too, so the seed flow is one call.
    placed = api_client.post(
        "/api/v1/placements",
        json={
            "item_id": str(cup_id),
            "slot_id": str(first),
            "note": "preserve me",
        },
        headers=seeded_actor.headers(),
    )
    assert placed.status_code == 201, placed.text
    old_id = placed.json()["id"]

    resp = _patch(api_client, seeded_actor, old_id, {"slot_id": str(second)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["note"] == "preserve me"


async def test_patch_placement_both_fields_uses_new_note(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Both `note` and `slot_id` provided → new row's note = explicit new value."""
    cup_id = storage_hierarchy.items["马克杯"]
    first = storage_hierarchy.slots["L1S1"]
    second = storage_hierarchy.slots["L1S2"]
    placed = api_client.post(
        "/api/v1/placements",
        json={"item_id": str(cup_id), "slot_id": str(first), "note": "old"},
        headers=seeded_actor.headers(),
    )
    old_id = placed.json()["id"]

    resp = _patch(
        api_client, seeded_actor, old_id,
        {"slot_id": str(second), "note": "explicit new"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slot_id"] == str(second)
    assert body["note"] == "explicit new"


async def test_patch_placement_no_fields_is_400(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Empty body → 400 ValidationFailedError.

    Body shape validation that FastAPI/Pydantic catches at the framework
    layer (extra fields, wrong types) returns 422; business-rule checks the
    route raises itself return 400 (project convention — see
    ``ValidationFailedError``). An empty body slips past Pydantic (every
    field optional) and hits the route's own check.
    """
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    resp = _patch(api_client, seeded_actor, placed.json()["id"], {})
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "validation_error"


async def test_patch_placement_extra_field_is_422(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """`{"hacker": true}` → 422 — schema validation (extra=forbid)."""
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    resp = _patch(
        api_client, seeded_actor, placed.json()["id"], {"note": "x", "hacker": True}
    )
    assert resp.status_code == 422


async def test_patch_placement_unknown_id_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = _patch(api_client, seeded_actor, uuid.uuid4(), {"note": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patch_placement_cross_home_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """The placement exists, but the caller names a different home → 404."""
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]

    resp = api_client.patch(
        f"/api/v1/placements/{placement_id}",
        json={"note": "x"},
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patch_placement_cross_home_slot_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """PATCH with a foreign `slot_id` → 404 (not a half-applied move)."""
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]

    foreign_slot = await _make_foreign_slot(db_engine)
    resp = _patch(
        api_client, seeded_actor, placement_id, {"slot_id": str(foreign_slot)}
    )
    assert resp.status_code == 404, resp.text

    # No second placement row should have been written.
    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(
                    ItemPlacement.item_id == storage_hierarchy.items["马克杯"]
                )
            )
        ).scalars().all()
    assert len(rows) == 1


async def test_patch_placement_closed_is_409(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """A closed placement is read-only — PATCH on it → 409."""
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    placement_id = placed.json()["id"]
    closed = api_client.delete(
        f"/api/v1/placements/{placement_id}", headers=seeded_actor.headers()
    )
    assert closed.status_code == 200

    resp = _patch(api_client, seeded_actor, placement_id, {"note": "x"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


async def test_patch_placement_without_credentials_is_401(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    placed = _place(
        api_client,
        seeded_actor,
        storage_hierarchy.items["马克杯"],
        storage_hierarchy.slots["L1S1"],
    )
    resp = api_client.patch(
        f"/api/v1/placements/{placed.json()['id']}",
        json={"note": "x"},
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401


async def test_patch_placement_same_slot_is_noop(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """`slot_id` equal to the current slot → no new row, only the note changes."""
    cup_id = storage_hierarchy.items["马克杯"]
    slot_id = storage_hierarchy.slots["L1S1"]
    placed = _place(api_client, seeded_actor, cup_id, slot_id)
    placement_id = placed.json()["id"]

    resp = _patch(
        api_client,
        seeded_actor,
        placement_id,
        {"slot_id": str(slot_id), "note": "edited"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == placement_id
    assert resp.json()["note"] == "edited"

    async with _session(db_engine)() as session:
        rows = (
            await session.execute(
                select(ItemPlacement).where(ItemPlacement.item_id == cup_id)
            )
        ).scalars().all()
        assert len(rows) == 1, "same-slot PATCH must not insert a new row"


# ----------------------------------------------------------------- (helpers)
# `_patch` builds an actor.headers() call; the cross-home variant below
# overrides X-Home-Id. Kept inline above.
