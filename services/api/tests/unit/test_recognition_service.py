"""Tests for recognition_service.recognize_from_asset."""
from __future__ import annotations

import uuid

import pytest

from app.ai.providers.mock import MockAIProvider
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.db.enums import AssetStatus
from app.models import Asset
from app.services import recognition_service

pytestmark = pytest.mark.asyncio


async def _seed_asset(
    db_session, *, home_id: uuid.UUID, user_id: uuid.UUID, status: str = "ready"
) -> Asset:
    asset = Asset(
        home_id=home_id,
        created_by=user_id,
        bucket="uploads",
        object_key=f"home/{home_id}/x.png",
        content_type="image/png",
        size_bytes=10,
        sha256="0" * 64,
        status=status,
    )
    db_session.add(asset)
    await db_session.commit()
    await db_session.refresh(asset)
    return asset


async def test_recognition_happy_path(db_session, seeded_actor) -> None:
    # Use a real seeded actor's home/user so we exercise the cross-home guard.
    asset = await _seed_asset(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
    )
    provider = MockAIProvider()
    res = await recognition_service.recognize_from_asset(
        db_session,
        provider=provider,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        asset_id=asset.id,
        description="a cup",
    )
    assert res.output.name == "马克杯"
    assert res.trace_id is not None


async def test_recognition_rejects_wrong_home(db_session, seeded_actor) -> None:
    asset = await _seed_asset(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
    )
    other_home = uuid.uuid4()
    provider = MockAIProvider()
    with pytest.raises(NotFoundError):
        await recognition_service.recognize_from_asset(
            db_session,
            provider=provider,
            home_id=other_home,
            user_id=seeded_actor.user_id,
            asset_id=asset.id,
        )


async def test_recognition_rejects_pending_asset(db_session, seeded_actor) -> None:
    asset = await _seed_asset(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
        status=AssetStatus.PENDING.value,
    )
    provider = MockAIProvider()
    with pytest.raises(ValidationFailedError):
        await recognition_service.recognize_from_asset(
            db_session,
            provider=provider,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            asset_id=asset.id,
        )


async def test_recognition_rejects_missing_asset(db_session, seeded_actor) -> None:
    provider = MockAIProvider()
    with pytest.raises(NotFoundError):
        await recognition_service.recognize_from_asset(
            db_session,
            provider=provider,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            asset_id=uuid.uuid4(),
        )


async def test_recognition_bubbles_provider_error(db_session, seeded_actor) -> None:
    asset = await _seed_asset(
        db_session,
        home_id=seeded_actor.home_id,
        user_id=seeded_actor.user_id,
    )
    from app.ai.errors import AIProviderTransportError

    provider = MockAIProvider(vision_side_effect=AIProviderTransportError("net"))
    with pytest.raises(AIProviderTransportError):
        await recognition_service.recognize_from_asset(
            db_session,
            provider=provider,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
            asset_id=asset.id,
        )
