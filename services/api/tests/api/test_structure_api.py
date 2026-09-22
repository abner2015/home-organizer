"""Integration tests for the storage-structure write API.

The five routers here are what let a user build the hierarchy the agent ranks
against; before them the only way to create a room was ``python -m app.db.seed``.
Covered:

- Four levels create with 201 and the right shape, and the created slot is then
  visible through the *read* API (the end-to-end point of the whole batch).
- Deleting a node with children → 409, in that node's own words.
- ``delete_slot`` → 409 on *removed* placements too, which is the case the
  design doc got wrong (``slot_id`` is ``ondelete="RESTRICT"``, so an
  active-only check would pass here and then 500 in the database).
- Cross-home ids → 404, unknown ids → 404.
- Enum-typed bodies reject ``"厨房"`` with a 422 rather than a CHECK violation.
- ``PATCH /homes/{id}`` is owner-only.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Home, HomeMembership, ItemPlacement, StorageSlot
from app.models.room import Room
from app.models.storage import StorageSection, StorageUnit

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------- helpers


def _create_room(client: TestClient, actor, name="书房", room_type="study", **extra):
    return client.post(
        f"/api/v1/homes/{actor.home_id}/rooms",
        json={"name": name, "room_type": room_type, **extra},
        headers=actor.headers(),
    )


async def _full_chain(client: TestClient, actor) -> dict[str, str]:
    """Create room → unit → section → slot, returning every id."""
    room = _create_room(client, actor)
    assert room.status_code == 201, room.text
    room_id = room.json()["id"]

    unit = client.post(
        f"/api/v1/rooms/{room_id}/storage-units",
        json={"name": "书柜", "unit_type": "cabinet"},
        headers=actor.headers(),
    )
    assert unit.status_code == 201, unit.text
    unit_id = unit.json()["id"]

    section = client.post(
        f"/api/v1/storage-units/{unit_id}/sections",
        json={"name": "第1层", "section_type": "layer"},
        headers=actor.headers(),
    )
    assert section.status_code == 201, section.text
    section_id = section.json()["id"]

    slot = client.post(
        f"/api/v1/sections/{section_id}/slots",
        json={"code": "A1", "label": "左半边"},
        headers=actor.headers(),
    )
    assert slot.status_code == 201, slot.text
    return {
        "room_id": room_id,
        "unit_id": unit_id,
        "section_id": section_id,
        "slot_id": slot.json()["id"],
    }


# -------------------------------------------------------------------- creates


async def test_create_room_returns_201_and_view(api_client, seeded_actor) -> None:
    resp = _create_room(api_client, seeded_actor)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "书房"
    assert body["room_type"] == "study"
    assert body["home_id"] == str(seeded_actor.home_id)
    assert body["unit_count"] == 0
    assert body["sort_order"] == 0


async def test_create_assigns_increasing_sort_order(
    api_client, seeded_actor
) -> None:
    """Siblings must not all sit at the default 0 — that degrades to name order."""
    first = _create_room(api_client, seeded_actor, name="书房")
    second = _create_room(api_client, seeded_actor, name="储藏间")

    assert first.json()["sort_order"] == 0
    assert second.json()["sort_order"] == 1


async def test_new_slot_is_reachable_through_the_read_api(
    api_client, seeded_actor
) -> None:
    """The point of the whole batch: a user-created slot shows up in the tree."""
    ids = await _full_chain(api_client, seeded_actor)

    tree = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/space-tree",
        headers=seeded_actor.headers(),
    )
    assert tree.status_code == 200, tree.text
    rooms = tree.json()["rooms"]
    assert [r["name"] for r in rooms] == ["书房"]

    room = rooms[0]
    assert room["unit_count"] == 1
    unit = room["units"][0]
    assert unit["name"] == "书柜"
    section = unit["sections"][0]
    assert section["name"] == "第1层"
    assert [s["code"] for s in section["slots"]] == ["A1"]
    assert section["slots"][0]["id"] == ids["slot_id"]


async def test_capacity_hint_stays_free_text(api_client, seeded_actor) -> None:
    """``"6"`` is meaningful to the engine and must survive the round trip.

    ``candidate_gen._parse_capacity`` reads a bare number as a count, so
    narrowing the field to ``Literal["small","medium","large"]`` would make the
    API unable to express something the ranking code already handles.
    """
    ids = await _full_chain(api_client, seeded_actor)

    patch = api_client.patch(
        f"/api/v1/slots/{ids['slot_id']}",
        json={"capacity_hint": "6"},
        headers=seeded_actor.headers(),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["capacity_hint"] == "6"

    slots = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/slots",
        headers=seeded_actor.headers(),
    ).json()
    assert slots[0]["capacity_hint"] == "6"


async def test_duplicate_code_in_same_section_is_409(
    api_client, seeded_actor
) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.post(
        f"/api/v1/sections/{ids['section_id']}/slots",
        json={"code": "A1"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "conflict"


async def test_same_code_in_a_different_section_is_fine(
    api_client, seeded_actor
) -> None:
    """The unique index is per section, not per home."""
    ids = await _full_chain(api_client, seeded_actor)
    other_section = api_client.post(
        f"/api/v1/storage-units/{ids['unit_id']}/sections",
        json={"name": "第2层", "section_type": "layer"},
        headers=seeded_actor.headers(),
    ).json()

    resp = api_client.post(
        f"/api/v1/sections/{other_section['id']}/slots",
        json={"code": "A1"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 201, resp.text


async def test_rename_slot_code_onto_a_taken_code_is_409(
    api_client, seeded_actor
) -> None:
    """A PATCH that moves ``code`` re-checks uniqueness instead of 500-ing."""
    ids = await _full_chain(api_client, seeded_actor)
    sibling = api_client.post(
        f"/api/v1/sections/{ids['section_id']}/slots",
        json={"code": "A2"},
        headers=seeded_actor.headers(),
    ).json()

    resp = api_client.patch(
        f"/api/v1/slots/{sibling['id']}",
        json={"code": "A1"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------- validation


async def test_unknown_room_type_is_422_not_500(
    api_client, seeded_actor
) -> None:
    """Enum-typed field: the edge rejects it, the CHECK constraint never runs."""
    resp = _create_room(api_client, seeded_actor, room_type="厨房")
    assert resp.status_code == 422, resp.text


async def test_unknown_field_is_rejected(api_client, seeded_actor) -> None:
    resp = _create_room(api_client, seeded_actor, colour="red")
    assert resp.status_code == 422


async def test_explicit_null_on_a_required_field_is_400(
    api_client, seeded_actor
) -> None:
    resp = _create_room(api_client, seeded_actor)
    room_id = resp.json()["id"]

    patch = api_client.patch(
        f"/api/v1/rooms/{room_id}",
        json={"name": None},
        headers=seeded_actor.headers(),
    )
    assert patch.status_code == 400, patch.text


async def test_patch_leaves_omitted_fields_alone(api_client, seeded_actor) -> None:
    resp = _create_room(api_client, seeded_actor, name="书房")
    room_id = resp.json()["id"]

    patch = api_client.patch(
        f"/api/v1/rooms/{room_id}",
        json={"name": "工作间"},
        headers=seeded_actor.headers(),
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["name"] == "工作间"
    assert patch.json()["room_type"] == "study"


# --------------------------------------------------------------- auth / scope


async def test_create_without_credentials_is_401(api_client, seeded_actor) -> None:
    """``X-Home-Id`` alone buys nothing — identity comes from the signed token."""
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/rooms",
        json={"name": "书房", "room_type": "study"},
        headers={"X-Home-Id": str(seeded_actor.home_id)},
    )
    assert resp.status_code == 401, resp.text


async def test_create_in_a_non_uuid_home_is_400(api_client, seeded_actor) -> None:
    """A malformed selector is a client bug, not a missing one. The read routes
    never see this because ``Header`` rejects absence earlier."""
    resp = api_client.post(
        f"/api/v1/homes/{seeded_actor.home_id}/rooms",
        json={"name": "书房", "room_type": "study"},
        headers={**seeded_actor.headers(), "X-Home-Id": "not-a-uuid"},
    )
    assert resp.status_code == 400, resp.text


async def test_create_room_in_another_home_is_404(
    api_client, seeded_actor, db_engine
) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Home(id=uuid.uuid4(), name="别人的家", owner_id=uuid.uuid4()))
        await session.commit()

    stranger = uuid.uuid4()
    resp = api_client.post(
        f"/api/v1/homes/{stranger}/rooms",
        json={"name": "书房", "room_type": "study"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_cross_home_patch_is_404(api_client, seeded_actor, db_engine) -> None:
    """Patching by id alone must not reach a row in someone else's home."""
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.patch(
        f"/api/v1/rooms/{ids['room_id']}",
        json={"name": "改名"},
        # A well-formed token for a home the caller is *not* in.
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_cross_home_delete_is_404(api_client, seeded_actor) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.delete(
        f"/api/v1/rooms/{ids['room_id']}",
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


async def test_unknown_id_is_404(api_client, seeded_actor) -> None:
    resp = api_client.patch(
        f"/api/v1/slots/{uuid.uuid4()}",
        json={"label": "x"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_rename_home_by_non_owner_is_403(
    api_client, seeded_actor, db_engine
) -> None:
    """A member who is not the owner gets 403 — the one place 403 is right."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        membership = (
            await session.execute(
                select(HomeMembership).where(
                    HomeMembership.home_id == seeded_actor.home_id,
                    HomeMembership.user_id == seeded_actor.user_id,
                )
            )
        ).scalar_one()
        membership.role = "member"
        await session.commit()

    resp = api_client.patch(
        f"/api/v1/homes/{seeded_actor.home_id}",
        json={"name": "新名字"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_rename_home_as_owner(api_client, seeded_actor) -> None:
    """The seeded actor owns their home (``seeded_actor`` creates it that way)."""
    resp = api_client.patch(
        f"/api/v1/homes/{seeded_actor.home_id}",
        json={"name": "我的新家"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "我的新家"


# -------------------------------------------------------------------- deletes


async def test_delete_room_with_units_is_409(api_client, seeded_actor) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.delete(
        f"/api/v1/rooms/{ids['room_id']}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["details"]["unit_count"] == 1


async def test_delete_unit_with_sections_is_409(api_client, seeded_actor) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.delete(
        f"/api/v1/storage-units/{ids['unit_id']}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["details"]["section_count"] == 1


async def test_delete_section_with_slots_is_409(api_client, seeded_actor) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    resp = api_client.delete(
        f"/api/v1/sections/{ids['section_id']}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["details"]["slot_count"] == 1


async def test_delete_empty_chain_bottoms_up(api_client, seeded_actor) -> None:
    """Deleting from the leaves leaves nothing behind."""
    ids = await _full_chain(api_client, seeded_actor)

    for path, key in (
        ("slots", "slot_id"),
        ("sections", "section_id"),
        ("storage-units", "unit_id"),
        ("rooms", "room_id"),
    ):
        resp = api_client.delete(
            f"/api/v1/{path}/{ids[key]}", headers=seeded_actor.headers()
        )
        assert resp.status_code == 204, f"{path}: {resp.text}"

    tree = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/space-tree",
        headers=seeded_actor.headers(),
    ).json()
    assert tree["rooms"] == []


async def test_delete_slot_with_only_history_is_still_409(
    api_client, seeded_actor, db_engine
) -> None:
    """A *removed* placement still blocks the delete.

    ``item_placements.slot_id`` is ``ondelete="RESTRICT"`` and the database does
    not care that the placement was since removed. An app-level check that
    counted only active rows would let this through and then fail with an
    ``IntegrityError`` — a 500 where the honest answer is "this position has
    history".
    """
    ids = await _full_chain(api_client, seeded_actor)

    from app.models.item import Item

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        item = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="旧台灯",
            category="decor",
            created_by=seeded_actor.user_id,
        )
        session.add(item)
        await session.flush()
        session.add(
            ItemPlacement(
                id=uuid.uuid4(),
                item_id=item.id,
                slot_id=uuid.UUID(ids["slot_id"]),
                placed_by=seeded_actor.user_id,
                source="user_manual",
                removed_at=func.current_timestamp(),
            )
        )
        await session.commit()

    resp = api_client.delete(
        f"/api/v1/slots/{ids['slot_id']}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 409, resp.text
    details = resp.json()["error"]["details"]
    assert details == {"active_count": 0, "historical_count": 1}


async def test_delete_slot_with_an_active_placement_is_409(
    api_client, seeded_actor, db_engine
) -> None:
    ids = await _full_chain(api_client, seeded_actor)

    from app.models.item import Item

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        item = Item(
            id=uuid.uuid4(),
            home_id=seeded_actor.home_id,
            name="台灯",
            category="decor",
            created_by=seeded_actor.user_id,
        )
        session.add(item)
        await session.flush()
        session.add(
            ItemPlacement(
                id=uuid.uuid4(),
                item_id=item.id,
                slot_id=uuid.UUID(ids["slot_id"]),
                placed_by=seeded_actor.user_id,
                source="user_manual",
            )
        )
        await session.commit()

    resp = api_client.delete(
        f"/api/v1/slots/{ids['slot_id']}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 409, resp.text
    details = resp.json()["error"]["details"]
    assert details == {"active_count": 1, "historical_count": 0}


async def test_deleting_a_parent_does_not_cascade(api_client, seeded_actor, db_engine) -> None:
    """The 409s are the only thing standing between a PATCH and a lost subtree."""
    ids = await _full_chain(api_client, seeded_actor)

    api_client.delete(
        f"/api/v1/storage-units/{ids['unit_id']}", headers=seeded_actor.headers()
    )

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert (
            await session.execute(select(func.count()).select_from(Room))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count()).select_from(StorageUnit))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count()).select_from(StorageSection))
        ).scalar_one() == 1
        assert (
            await session.execute(select(func.count()).select_from(StorageSlot))
        ).scalar_one() == 1
