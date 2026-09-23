"""Integration tests for the home / storage-structure read API (Phase 10).

Covers the six routes added so the Web app can render the whole
home → room → unit → section → slot hierarchy:

- ``GET /api/v1/homes``                 — only the caller's homes, with counts.
- ``GET /api/v1/homes/{id}``            — single home; non-member → 404.
- ``GET /api/v1/homes/{id}/rooms``      — room list with ``unit_count``.
- ``GET /api/v1/homes/{id}/space-tree`` — the nested tree.
- ``GET /api/v1/homes/{id}/slots``      — flat slot list.
- ``GET /api/v1/rooms/{id}/storage-units`` — one room's units, nested.

Cross-home access returns 404 (never 403), so the API never leaks the
existence of another home's data.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models import HomeMembership

pytestmark = pytest.mark.asyncio


# ------------------------------------------------------------------ list homes


async def test_list_homes_returns_membership_only(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """The caller sees their own home, with member / item / rule counts."""
    resp = api_client.get("/api/v1/homes", headers=seeded_actor.headers())
    assert resp.status_code == 200, resp.text
    homes = resp.json()
    assert len(homes) == 1
    home = homes[0]
    assert home["id"] == str(seeded_actor.home_id)
    assert home["name"] == "Test Home"
    assert home["member_count"] == 1
    # storage_hierarchy seeds two items (马克杯 / 处方药).
    assert home["item_count"] == 2
    assert home["rule_count"] == 0


async def test_list_homes_excludes_other_homes(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """A home the caller is not a member of never appears."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import Home

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Home(id=uuid.uuid4(), name="别人的家", owner_id=uuid.uuid4()))
        await session.commit()

    resp = api_client.get("/api/v1/homes", headers=seeded_actor.headers())
    assert resp.status_code == 200
    assert [h["name"] for h in resp.json()] == ["Test Home"]


# -------------------------------------------------------------------- one home


async def test_get_home_non_member_is_404(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Unknown id and non-member id are indistinguishable (both 404)."""
    resp = api_client.get(
        f"/api/v1/homes/{uuid.uuid4()}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


async def test_get_home_happy_path(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(seeded_actor.home_id)
    assert body["owner_id"] == str(seeded_actor.user_id)


# ----------------------------------------------------------------------- rooms


async def test_list_rooms_includes_unit_count(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/rooms", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    rooms = {r["name"]: r for r in resp.json()}
    assert set(rooms) == {"客厅", "厨房", "主卧"}
    assert rooms["客厅"]["unit_count"] == 1
    assert rooms["客厅"]["room_type"] == "living"
    # Ordered by sort_order.
    assert [r["name"] for r in resp.json()] == ["客厅", "厨房", "主卧"]


async def test_list_rooms_other_home_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        f"/api/v1/homes/{uuid.uuid4()}/rooms", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ space tree


async def test_space_tree_is_fully_nested(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/space-tree",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    tree = resp.json()
    assert tree["home"]["id"] == str(seeded_actor.home_id)
    assert len(tree["rooms"]) == 3

    living = next(r for r in tree["rooms"] if r["name"] == "客厅")
    assert living["unit_count"] == 1
    unit = living["units"][0]
    assert unit["name"] == "客厅装饰柜"

    sections = {s["name"]: s for s in unit["sections"]}
    assert set(sections) == {"左玻璃柜", "右玻璃柜", "中间开放区"}
    layer = sections["左玻璃柜"]
    assert layer["section_type"] == "layer"
    assert [s["code"] for s in layer["slots"]] == ["L1", "L2", "L3"]
    # The join back to home/room metadata survives the tree walk.
    assert layer["slots"][0]["section_id"] == layer["id"]
    assert layer["slots"][0]["allowed_categories"] == ["decor"]


async def test_space_tree_empty_home_has_no_rooms(
    api_client: TestClient, seeded_actor
) -> None:
    """A home with no rooms is still a valid (empty) tree, not a 404."""
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/space-tree",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["rooms"] == []


# ----------------------------------------------------------------------- slots


async def test_list_slots_is_flat_and_enriched(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        f"/api/v1/homes/{seeded_actor.home_id}/slots", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    slots = resp.json()
    assert len(slots) == len(storage_hierarchy.slots)
    by_code = {s["code"]: s for s in slots}
    assert by_code["L1S1"]["allowed_categories"] == ["utensil"]
    # No placements in this fixture, so every slot reads as empty.
    assert by_code["L1S1"]["active_count"] == 0


# ------------------------------------------------------- room → storage units


async def test_list_storage_units_for_room(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    resp = api_client.get(
        f"/api/v1/rooms/{storage_hierarchy.living_room_id}/storage-units",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    units = resp.json()
    assert [u["name"] for u in units] == ["客厅装饰柜"]
    assert units[0]["room_id"] == str(storage_hierarchy.living_room_id)


async def test_list_storage_units_unknown_room_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.get(
        f"/api/v1/rooms/{uuid.uuid4()}/storage-units", headers=seeded_actor.headers()
    )
    assert resp.status_code == 404


async def test_list_storage_units_other_home_room_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    """A real room id that lives in somebody else's home still 404s."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.enums import RoomType
    from app.models import Home
    from app.models.room import Room

    other_home_id = uuid.uuid4()
    other_room_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Home(id=other_home_id, name="别人的家", owner_id=uuid.uuid4()))
        session.add(
            Room(
                id=other_room_id,
                home_id=other_home_id,
                name="别人的客厅",
                room_type=RoomType.LIVING.value,
                sort_order=1,
            )
        )
        await session.commit()

    resp = api_client.get(
        f"/api/v1/rooms/{other_room_id}/storage-units",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------------ auth


async def test_missing_credentials_are_rejected(api_client: TestClient) -> None:
    """No Bearer token → 401, never a data leak."""
    resp = api_client.get("/api/v1/homes")
    assert resp.status_code == 401


async def test_spoofed_user_header_grants_nothing(
    api_client: TestClient, seeded_actor
) -> None:
    """The pre-JWT stub headers are inert: identity comes from the signed token.

    Before P0.1a any caller could name themselves with ``X-User-Id``. Those
    headers must now buy exactly nothing.
    """
    resp = api_client.get(
        "/api/v1/homes",
        headers={
            "X-User-Id": str(seeded_actor.user_id),
            "X-Home-Id": str(seeded_actor.home_id),
        },
    )
    assert resp.status_code == 401, resp.text


async def test_non_member_home_is_404_not_403(
    api_client: TestClient, seeded_actor
) -> None:
    """A valid token naming someone else's home must not confirm it exists."""
    resp = api_client.get(
        f"/api/v1/homes/{uuid.uuid4()}/space-tree",
        headers={**seeded_actor.headers(), "X-Home-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404, resp.text


_ = HomeMembership


# ------------------------------------------------------------------ create home
#
# P0.9 added ``POST /api/v1/homes``. Until then ``auth_service.signup`` was the
# only path that ever created a Home, so the unit tests for the new primitive
# live in ``tests/unit/test_home_service.py``; here we only assert the *route
# surface* — status codes, the shape returned by ``HomeView``, and the auth
# invariants that depend on the route (not the service).


async def test_create_home_happy_path(
    api_client: TestClient, seeded_actor
) -> None:
    """A bearer call without ``X-Home-Id`` returns 201 + HomeView.

    The caller becomes the new home's OWNER, so the project convention
    "non-members 404, non-owners 403" still holds: ``GET /homes/{new_id}``
    should immediately work, and the actor's home list grows by one.
    """
    token = _sign(seeded_actor.user_id)
    resp = api_client.post(
        "/api/v1/homes",
        # No X-Home-Id — there is no home to put in the header yet, and the
        # whole point of POST is that you create one *after* the call.
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "老家"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "老家"
    assert body["owner_id"] == str(seeded_actor.user_id)
    # Fresh home, fresh counts.
    assert body["member_count"] == 1
    assert body["item_count"] == 0
    assert body["rule_count"] == 0
    assert body["timezone"] == "Asia/Shanghai"

    # And it really is discoverable as a member.
    new_id = body["id"]
    get = api_client.get(
        f"/api/v1/homes/{new_id}",
        headers={"Authorization": f"Bearer {token}", "X-Home-Id": new_id},
    )
    assert get.status_code == 200, get.text
    assert get.json()["name"] == "老家"


async def test_create_home_then_list_includes_new_one(
    api_client: TestClient, seeded_actor
) -> None:
    """``GET /homes`` picks up the freshly-created one without a refresh."""
    token = _sign(seeded_actor.user_id)
    create = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "工作室"},
    )
    assert create.status_code == 201, create.text

    listed = api_client.get(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert listed.status_code == 200, listed.text
    names = [h["name"] for h in listed.json()]
    # Oldest first per ``GET /homes`` ordering; ``Test Home`` came first.
    assert names == ["Test Home", "工作室"]


async def test_create_home_no_bearer_is_401(api_client: TestClient) -> None:
    """Missing bearer → 401. ``X-Home-Id`` is irrelevant on this route."""
    resp = api_client.post("/api/v1/homes", json={"name": "没 token"})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "unauthenticated"


async def test_create_home_empty_name_after_strip_is_400(
    api_client: TestClient, seeded_actor
) -> None:
    """Pure-whitespace name is a business-validation 400 (P0.7 convention).

    Pydantic's ``min_length=1`` would let ``"   "`` through (length-3); the
    service layer ``str.strip`` then catches it and the route raises
    ``ValidationFailedError`` so the user sees a project-style 400, not the
    generic 422 Pydantic would emit for ``name: ""``.
    """
    resp = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {_sign(seeded_actor.user_id)}"},
        json={"name": "   "},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "name" in body["error"]["message"]


async def test_create_home_name_too_long_is_422(
    api_client: TestClient, seeded_actor
) -> None:
    """101 chars crosses the DB column bound → Pydantic 422."""
    resp = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {_sign(seeded_actor.user_id)}"},
        json={"name": "x" * 101},
    )
    assert resp.status_code == 422


async def test_create_home_extra_field_is_422(
    api_client: TestClient, seeded_actor
) -> None:
    """``extra="forbid"`` rejects anything the schema did not name."""
    resp = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {_sign(seeded_actor.user_id)}"},
        json={"name": "ok", "is_admin": True},
    )
    assert resp.status_code == 422


async def test_create_home_explicit_timezone_is_honored(
    api_client: TestClient, seeded_actor
) -> None:
    """Non-default timezone round-trips through the response body."""
    resp = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {_sign(seeded_actor.user_id)}"},
        json={"name": "NY apt", "timezone": "America/New_York"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["timezone"] == "America/New_York"


async def test_create_home_strips_whitespace_around_name(
    api_client: TestClient, seeded_actor
) -> None:
    """``" 老家 "`` round-trips as ``"老家"``."""
    resp = api_client.post(
        "/api/v1/homes",
        headers={"Authorization": f"Bearer {_sign(seeded_actor.user_id)}"},
        json={"name": " 老家 "},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"] == "老家"


def _sign(user_id: uuid.UUID) -> str:
    """Mint a real access token — the route's auth path is JWT, not a stub."""
    from app.services.security import create_access_token

    return create_access_token(user_id)
