"""Integration tests for the item write API (Phase 11 web wiring).

Three routes the Web app's "添加物品" flow needs and didn't have:

- ``POST  /api/v1/uploads/presign``      — a presigned PUT ticket (docs/API.md §6).
- ``POST  /api/v1/items``                — create, optionally with image keys.
- ``PATCH /api/v1/items/{id}``           — apply the user's edits from the
  vision-confirm step so they survive into the recommendation run.
- ``POST  /api/v1/items/{id}/vision``    — recognise the primary image and
  write the result back onto the item.

Two behaviours are load-bearing and pinned below: an image key from another
home is rejected (400), and the vision view never invents a ``confidence`` the
Phase 4 schema doesn't produce.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.ai.providers.mock import MockAIProvider
from app.api.v1.items import _get_ai_provider
from app.main import app
from app.storage.backend import get_storage
from tests.api.conftest import make_png_bytes

pytestmark = pytest.mark.asyncio


def _upload_bytes(key: str) -> None:
    """Put real image bytes at a presigned key.

    The item-vision route inlines the stored object as a data URI, so unlike
    the old presign-only path it actually needs the bytes to exist.
    """
    get_storage().put(key, make_png_bytes(), "image/png")


def _override_provider(mock: MockAIProvider) -> None:
    def _get() -> MockAIProvider:
        return mock

    app.dependency_overrides[_get_ai_provider] = _get


# ------------------------------------------------------------------- presign


async def test_presign_returns_a_key_under_the_callers_home(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "tea.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == "PUT"
    assert body["expires_in"] > 0
    # The original filename never becomes the key, and the key is home-scoped.
    assert body["object_key"].startswith(f"home/{seeded_actor.home_id}/")
    assert "tea" not in body["object_key"]
    assert body["object_key"].endswith(".png")
    assert body["object_key"] in body["upload_url"]


async def test_presign_rejects_unsupported_content_type(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.gif", "content_type": "image/gif"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "unsupported_content_type"


async def test_presign_rejects_extra_fields(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png", "bucket": "evil"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


# -------------------------------------------------------------------- create


async def test_create_item_without_images(
    api_client: TestClient, seeded_actor
) -> None:
    """The manual path: no photo, just the fields the user typed."""
    resp = api_client.post(
        "/api/v1/items",
        json={"name": "雨伞", "category": "misc", "estimated_size": "medium"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "雨伞"
    assert body["home_id"] == str(seeded_actor.home_id)
    assert body["current_placement"] is None
    assert body["image_urls"] == []

    # It shows up in the list straight away.
    listed = api_client.get(
        "/api/v1/items", params={"q": "雨伞"}, headers=seeded_actor.headers()
    )
    assert [i["name"] for i in listed.json()["items"]] == ["雨伞"]


async def test_create_item_with_uploaded_image(
    api_client: TestClient, seeded_actor
) -> None:
    presign = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    ).json()
    key = presign["object_key"]

    resp = api_client.post(
        "/api/v1/items",
        json={
            "name": "龙井茶叶",
            "category": "food",
            "image_object_keys": [key],
            "primary_image_object_key": key,
        },
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["image_urls"]) == 1
    assert body["image_urls"][0] == body["primary_image_url"]
    assert key in body["primary_image_url"]


async def test_create_item_rejects_another_homes_image_key(
    api_client: TestClient, seeded_actor
) -> None:
    foreign_key = f"home/{uuid.uuid4()}/2026/09/abc.png"
    resp = api_client.post(
        "/api/v1/items",
        json={"name": "偷来的图", "image_object_keys": [foreign_key]},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "validation_error"


async def test_create_item_rejects_primary_key_not_in_image_list(
    api_client: TestClient, seeded_actor
) -> None:
    key = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    ).json()["object_key"]

    resp = api_client.post(
        "/api/v1/items",
        json={
            "name": "无主图",
            "image_object_keys": [],
            "primary_image_object_key": key,
        },
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 400


async def test_create_item_rejects_extra_fields(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        "/api/v1/items",
        json={"name": "x", "is_admin": True},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


async def test_create_item_requires_a_name(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.post(
        "/api/v1/items", json={}, headers=seeded_actor.headers()
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------- patch


async def test_patch_applies_only_the_provided_fields(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    """Omitted keys must not be nulled out — the UI sends a sparse diff."""
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.patch(
        f"/api/v1/items/{cup_id}",
        json={"name": "陶瓷马克杯", "is_sensitive": True, "needs_lock": True},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "陶瓷马克杯"
    assert body["is_sensitive"] is True
    assert body["needs_lock"] is True
    # Untouched fields survive.
    assert body["category"] == "utensil"
    assert body["estimated_size"] == "small"


async def test_patch_clears_a_field_when_explicitly_null(
    api_client: TestClient, seeded_actor, storage_hierarchy
) -> None:
    cup_id = storage_hierarchy.items["马克杯"]
    resp = api_client.patch(
        f"/api/v1/items/{cup_id}",
        json={"description": None},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] is None


async def test_patch_unknown_item_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    resp = api_client.patch(
        f"/api/v1/items/{uuid.uuid4()}",
        json={"name": "x"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_patch_other_home_item_is_404(
    api_client: TestClient, seeded_actor, storage_hierarchy, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.item import Item

    foreign_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Item(
                id=foreign_id,
                home_id=uuid.uuid4(),
                name="别人的杯子",
                created_by=uuid.uuid4(),
            )
        )
        await session.commit()

    resp = api_client.patch(
        f"/api/v1/items/{foreign_id}",
        json={"name": "改名"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


# -------------------------------------------------------------------- vision


async def test_vision_recognizes_and_applies_to_the_item(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """A created-with-photo item gets its name/category filled in."""
    key = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    ).json()["object_key"]
    _upload_bytes(key)
    created = api_client.post(
        "/api/v1/items",
        json={
            "name": "未命名物品",
            "image_object_keys": [key],
            "primary_image_object_key": key,
        },
        headers=seeded_actor.headers(),
    ).json()
    item_id = created["id"]
    assert created["name"] == "未命名物品"

    mock = MockAIProvider()
    _override_provider(mock)

    resp = api_client.post(
        f"/api/v1/items/{item_id}/vision",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_id"] == item_id
    assert body["trace_id"]
    vision = body["vision"]
    # MockAIProvider's default vision payload.
    assert vision["name"] == "马克杯"
    assert vision["category"] == "厨房"
    assert vision["subcategory"] == "杯具"
    assert vision["description"] == "陶瓷材质"  # ← VisionOutput.notes
    assert vision["estimated_size"] == "small"  # ← VisionOutput.size_class
    # Pinned: no ``confidence`` is fabricated — the Phase 4 schema has none.
    assert vision["confidence"] is None
    assert vision["is_sensitive"] is False
    assert vision["needs_lock"] is False
    assert vision["attributes"] == []

    # The result was written back, so GET /items shows it too.
    fetched = api_client.get(
        f"/api/v1/items/{item_id}", headers=seeded_actor.headers()
    ).json()
    assert fetched["name"] == "马克杯"
    assert fetched["category"] == "厨房"
    assert fetched["estimated_size"] == "small"
    assert fetched["is_sensitive"] is False
    assert fetched["needs_lock"] is False

    # And the vision call was traced.
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models import AgentTrace

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        trace = (
            await session.execute(
                select(AgentTrace).where(AgentTrace.id == uuid.UUID(body["trace_id"]))
            )
        ).scalar_one()
        assert trace.home_id == seeded_actor.home_id


async def test_vision_keeps_a_user_supplied_description(
    api_client: TestClient, seeded_actor
) -> None:
    """Recognition fills the description only when the user left it empty."""
    key = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    ).json()["object_key"]
    _upload_bytes(key)
    created = api_client.post(
        "/api/v1/items",
        json={
            "name": "我的杯子",
            "description": "陪伴我很多年",
            "image_object_keys": [key],
            "primary_image_object_key": key,
        },
        headers=seeded_actor.headers(),
    ).json()

    _override_provider(MockAIProvider())
    resp = api_client.post(
        f"/api/v1/items/{created['id']}/vision", headers=seeded_actor.headers()
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["vision"]["description"] == "陶瓷材质"

    fetched = api_client.get(
        f"/api/v1/items/{created['id']}", headers=seeded_actor.headers()
    ).json()
    assert fetched["description"] == "陪伴我很多年"


async def test_vision_writes_back_sensitivity_flags(
    api_client: TestClient, seeded_actor
) -> None:
    """Sensitivity drives the hard-safety verifier, so it must persist.

    Before ``vision.v2.md`` the model answered neither flag and the item kept
    whatever the user ticked; now the model's answer is authoritative.
    """
    key = api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
        headers=seeded_actor.headers(),
    ).json()["object_key"]
    _upload_bytes(key)
    created = api_client.post(
        "/api/v1/items",
        json={
            "name": "不知道是什么",
            "image_object_keys": [key],
            "primary_image_object_key": key,
        },
        headers=seeded_actor.headers(),
    ).json()
    assert created["is_sensitive"] is False

    payload = dict(MockAIProvider().vision_response)
    payload.update({"is_sensitive": True, "needs_lock": True, "name": "处方药"})
    _override_provider(MockAIProvider(vision_response=payload))

    resp = api_client.post(
        f"/api/v1/items/{created['id']}/vision",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["vision"]["is_sensitive"] is True
    assert resp.json()["vision"]["needs_lock"] is True

    fetched = api_client.get(
        f"/api/v1/items/{created['id']}", headers=seeded_actor.headers()
    ).json()
    assert fetched["is_sensitive"] is True
    assert fetched["needs_lock"] is True


async def test_vision_without_an_image_is_400(
    api_client: TestClient, seeded_actor
) -> None:
    created = api_client.post(
        "/api/v1/items",
        json={"name": "没有照片"},
        headers=seeded_actor.headers(),
    ).json()
    _override_provider(MockAIProvider())

    resp = api_client.post(
        f"/api/v1/items/{created['id']}/vision",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 400


async def test_vision_unknown_item_is_404(
    api_client: TestClient, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        f"/api/v1/items/{uuid.uuid4()}/vision",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


async def test_vision_other_home_item_is_404(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.item import Item

    foreign_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Item(
                id=foreign_id,
                home_id=uuid.uuid4(),
                name="别人的杯子",
                created_by=uuid.uuid4(),
            )
        )
        await session.commit()

    _override_provider(MockAIProvider())
    resp = api_client.post(
        f"/api/v1/items/{foreign_id}/vision",
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------- auth


async def test_write_routes_require_actor_headers(api_client: TestClient) -> None:
    assert api_client.post("/api/v1/items", json={"name": "x"}).status_code in {
        401,
        422,
    }
    assert api_client.post(
        "/api/v1/uploads/presign",
        json={"file_name": "x.png", "content_type": "image/png"},
    ).status_code in {401, 422}
