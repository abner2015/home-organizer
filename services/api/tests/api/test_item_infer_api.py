"""Tests for ``POST /api/v1/items/infer``.

The route fills an item's attributes from its *name*, so the Web app can
prefill the confirm form after the user types one. It is read-only apart from
the observability trace — no ``Item`` is created here.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.ai.providers.mock import MockAIProvider
from app.api.v1.items import _get_ai_provider
from app.main import app
from app.models import AgentTrace
from app.models.item import Item

pytestmark = pytest.mark.asyncio

_PAYLOAD = {
    "name": "雨伞",
    "category": "misc",
    "subcategory": "雨具",
    "description": "折叠长柄伞",
    "estimated_size": "medium",
    "is_sensitive": False,
    "needs_lock": False,
}


def _override(mock: MockAIProvider) -> None:
    app.dependency_overrides[_get_ai_provider] = lambda: mock


async def test_infer_returns_the_vision_shape(
    api_client: TestClient, seeded_actor
) -> None:
    _override(MockAIProvider(structured_output_response=_PAYLOAD))

    resp = api_client.post(
        "/api/v1/items/infer",
        json={"name": "雨伞", "description": "长柄的"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Same shape `POST /items/{id}/vision` returns, so the UI has one code path.
    assert set(body) == {"vision", "trace_id"}
    vision = body["vision"]
    assert vision["name"] == "雨伞"
    assert vision["category"] == "misc"
    assert vision["subcategory"] == "雨具"
    assert vision["description"] == "折叠长柄伞"
    assert vision["estimated_size"] == "medium"
    assert vision["is_sensitive"] is False
    assert vision["needs_lock"] is False
    assert vision["attributes"] == []
    assert body["trace_id"]


async def test_infer_tolerates_a_null_heavy_answer(
    api_client: TestClient, seeded_actor
) -> None:
    """The model is told to leave unknown fields blank and may answer null."""
    _override(
        MockAIProvider(
            structured_output_response={
                "name": "某种东西",
                "category": None,
                "subcategory": None,
                "description": None,
                "estimated_size": None,
            }
        )
    )
    resp = api_client.post(
        "/api/v1/items/infer",
        json={"name": "某种东西"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    vision = resp.json()["vision"]
    assert vision["category"] == ""
    assert vision["subcategory"] == ""
    assert vision["description"] == ""
    assert vision["estimated_size"] is None


async def test_infer_writes_a_trace_but_no_item(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    _override(MockAIProvider(structured_output_response=_PAYLOAD))
    before = await _count_items(db_engine)

    resp = api_client.post(
        "/api/v1/items/infer",
        json={"name": "雨伞"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200

    assert await _count_items(db_engine) == before

    trace_id = resp.json()["trace_id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        trace = (
            await session.execute(
                select(AgentTrace).where(AgentTrace.id == uuid.UUID(trace_id))
            )
        ).scalar_one()
    assert trace.item_id is None
    assert trace.home_id == seeded_actor.home_id


async def test_infer_rejects_extra_fields(
    api_client: TestClient, seeded_actor
) -> None:
    _override(MockAIProvider(structured_output_response=_PAYLOAD))
    resp = api_client.post(
        "/api/v1/items/infer",
        json={"name": "雨伞", "sneaky": "value"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {},  # name is required
        {"name": ""},  # min_length=1
        {"name": "x" * 201},  # max_length=200
        {"name": "ok", "description": "x" * 2001},
    ],
)
async def test_infer_validates_the_body(
    api_client: TestClient, seeded_actor, body: dict
) -> None:
    _override(MockAIProvider(structured_output_response=_PAYLOAD))
    resp = api_client.post(
        "/api/v1/items/infer", json=body, headers=seeded_actor.headers()
    )
    assert resp.status_code == 422


async def test_infer_requires_actor_headers(api_client: TestClient) -> None:
    resp = api_client.post("/api/v1/items/infer", json={"name": "雨伞"})
    assert resp.status_code in {401, 422}


# ------------------------------------------------------------------ helpers


async def _count_items(db_engine) -> int:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(Item))).scalar_one()
        )
