"""Integration tests for POST /api/v1/items/recognize."""
from __future__ import annotations

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.ai.providers.mock import MockAIProvider
from app.api.v1.items import _get_ai_provider
from app.db.enums import AssetStatus
from app.main import app
from app.models import AgentTrace, Asset

pytestmark = pytest.mark.asyncio


def _make_png_bytes() -> bytes:
    img = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _override_provider(mock: MockAIProvider):
    """Replace the FastAPI dependency on AIProvider for this test."""

    def _get() -> MockAIProvider:
        return mock

    # FastAPI dependency_overrides is keyed by the function the endpoint
    # actually calls via `Depends(...)`. The endpoint uses
    # `Depends(_get_ai_provider)`, so we must override *that* function,
    # not the underlying `get_provider`.
    app.dependency_overrides[_get_ai_provider] = _get
    return _get


async def _upload_asset(
    client: TestClient,
    headers: dict[str, str],
    body: bytes,
    *,
    content_type: str = "image/png",
) -> str:
    files = {"file": ("x.png", body, content_type)}
    resp = client.post("/api/v1/assets/upload", headers=headers, files=files)
    assert resp.status_code == 201, resp.text
    return resp.json()["asset_id"]


async def test_recognize_endpoint_happy_path(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """Full end-to-end: upload → recognize → trace persisted."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    # 1) upload an asset
    asset_id = await _upload_asset(
        api_client, seeded_actor.headers(), _make_png_bytes()
    )
    # 2) override AI provider to a mock
    mock = MockAIProvider()
    _override_provider(mock)

    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": asset_id, "description": "a cup"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["name"] == "马克杯"
    assert body["attempts"] == 1
    assert body["provider"] == "mock"
    assert body["trace_id"]

    # 3) verify trace row persisted and linked to the right home.
    async with factory() as session:
        row = (
            await session.execute(
                select(AgentTrace).where(AgentTrace.id == uuid.UUID(body["trace_id"]))
            )
        ).scalar_one()
        assert row.home_id == seeded_actor.home_id
        assert row.user_id == seeded_actor.user_id
        assert row.final_status == "success"


async def test_recognize_endpoint_missing_asset(
    api_client: TestClient, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": str(uuid.uuid4())},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_recognize_endpoint_provider_error(
    api_client: TestClient, seeded_actor
) -> None:
    from app.ai.errors import AIProviderAuthError

    asset_id = await _upload_asset(
        api_client, seeded_actor.headers(), _make_png_bytes()
    )
    _override_provider(MockAIProvider(vision_side_effect=AIProviderAuthError("bad key")))
    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": asset_id},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ai_auth_error"


async def test_recognize_endpoint_rejects_extra_fields(
    api_client: TestClient, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": str(uuid.uuid4()), "hacker": True},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


async def test_recognize_endpoint_requires_uuid(
    api_client: TestClient, seeded_actor
) -> None:
    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": "not-a-uuid"},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 422


async def test_recognize_endpoint_cross_home_is_404(
    api_client: TestClient, seeded_actor, db_engine
) -> None:
    """An asset owned by a different home must NOT be visible."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Create a "foreign" home + user + asset.
    other_user_id = uuid.uuid4()
    other_home_id = uuid.uuid4()
    async with factory() as session:
        foreign_asset = Asset(
            home_id=other_home_id,
            created_by=other_user_id,
            bucket="uploads",
            object_key=f"home/{other_home_id}/x.png",
            content_type="image/png",
            size_bytes=10,
            sha256="a" * 64,
            status=AssetStatus.READY.value,
        )
        session.add(foreign_asset)
        await session.commit()
        foreign_asset_id = str(foreign_asset.id)

    _override_provider(MockAIProvider())
    resp = api_client.post(
        "/api/v1/items/recognize",
        json={"asset_id": foreign_asset_id},
        headers=seeded_actor.headers(),
    )
    assert resp.status_code == 404
