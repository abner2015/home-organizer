"""Tests for vision_service.recognize_image.

These run against an in-memory SQLite DB (via the project's db_session
fixture) so we can assert AgentTrace persistence.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.ai.errors import (
    AIOutputIncompleteError,
    AIOutputParseError,
    AIProviderAuthError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)
from app.ai.providers.mock import MockAIProvider
from app.models import AgentTrace
from app.services import vision_service

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------- fixtures


def _provider(**kw) -> MockAIProvider:
    return MockAIProvider(**kw)


async def _run(provider, *, hint=None, image_url="http://minio/x.png"):
    """Helper: invoke vision_service without DB to assert side-effects on a mock."""
    # We pass a fake session that records calls; simplest is to use the
    # real DB-less path by providing no user/home and trace=None.
    return await vision_service.recognize_image(
        None,  # type: ignore[arg-type]  -- not used when trace=None + no user
        provider=provider,
        image_url=image_url,
        hint=hint,
    )


# --------------------------------------------------------------- success


async def test_success_first_try_returns_output() -> None:
    provider = _provider()
    res = await _run(provider)
    assert res.output.name == "马克杯"
    assert res.attempts == 1
    assert res.total_duration_ms >= 0
    assert res.trace_id is None  # no user/home/trace → not persisted
    assert len(res.steps) == 1
    assert res.steps[0]["parse_ok"] is True


# --------------------------------------------------------------- retry on parse


async def test_parse_error_retries_then_succeeds() -> None:
    """Parse fails first, succeeds second; only 2 attempts total."""
    bad_then_good = MockAIProvider()
    # Pre-program: first call returns bad JSON, second succeeds.
    call_n = {"n": 0}

    async def fake_vision(image_url, *, hint=None, timeout_s=30.0):
        call_n["n"] += 1
        if call_n["n"] == 1:
            raise AIOutputParseError(
                "bad json", snippet="not json", schema="VisionOutput"
            )
        from app.ai.provider import VisionOutput

        return VisionOutput.model_validate(
            {
                "name": "x",
                "category": "厨房",
                "usage_frequency": "low",
                "size_class": "small",
                "fragility": "low",
            }
        )

    bad_then_good.vision = fake_vision  # type: ignore[assignment]
    res = await _run(bad_then_good)
    assert res.attempts == 2
    assert res.output.name == "x"
    assert len(res.steps) == 2
    assert res.steps[0]["parse_ok"] is False
    assert res.steps[1]["parse_ok"] is True


async def test_parse_error_exhausted_bubbles_up() -> None:
    """Parse keeps failing; after MAX_PARSE_RETRIES we raise."""
    provider = MockAIProvider(
        vision_side_effect=AIOutputParseError(
            "still bad", snippet="x", schema="VisionOutput"
        )
    )
    with pytest.raises(AIOutputParseError):
        await _run(provider)
    assert provider.call_count == vision_service.MAX_PARSE_RETRIES + 1


async def test_incomplete_error_is_also_retried() -> None:
    provider = MockAIProvider(
        vision_side_effect=AIOutputIncompleteError(
            "missing name", snippet="{}", schema="VisionOutput"
        )
    )
    with pytest.raises(AIOutputIncompleteError):
        await _run(provider)
    # MAX_PARSE_RETRIES retries → MAX+1 calls total
    assert provider.call_count == vision_service.MAX_PARSE_RETRIES + 1


# --------------------------------------------------------------- retry on transport


async def test_transport_error_retries_then_succeeds() -> None:
    call_n = {"n": 0}

    async def fake_vision(image_url, *, hint=None, timeout_s=30.0):
        call_n["n"] += 1
        if call_n["n"] == 1:
            raise AIProviderTransportError("conn reset")
        from app.ai.provider import VisionOutput

        return VisionOutput.model_validate(
            {
                "name": "x",
                "category": "厨房",
                "usage_frequency": "low",
                "size_class": "small",
                "fragility": "low",
            }
        )

    provider = MockAIProvider()
    provider.vision = fake_vision  # type: ignore[assignment]
    res = await _run(provider)
    assert res.attempts == 2
    assert res.output.name == "x"


async def test_transport_error_exhausted_bubbles() -> None:
    provider = MockAIProvider(
        vision_side_effect=AIProviderTransportError("flaky")
    )
    with pytest.raises(AIProviderTransportError):
        await _run(provider)
    assert provider.call_count == vision_service.MAX_TRANSPORT_RETRIES + 1


# --------------------------------------------------------------- non-retryable


async def test_auth_error_does_not_retry() -> None:
    provider = MockAIProvider(vision_side_effect=AIProviderAuthError("bad key"))
    with pytest.raises(AIProviderAuthError):
        await _run(provider)
    assert provider.call_count == 1


# --------------------------------------------------------------- timeout


async def test_timeout_is_treated_as_transport() -> None:
    provider = MockAIProvider(vision_side_effect=AIProviderTimeoutError("slow"))
    with pytest.raises(AIProviderTimeoutError):
        await _run(provider)
    assert provider.call_count == vision_service.MAX_TRANSPORT_RETRIES + 1


# --------------------------------------------------------------- logging


async def test_safe_log_payload_does_not_leak_image_url(caplog) -> None:
    provider = _provider()
    secret_url = "http://minio.example/foo?X-Amz-Signature=ABCDEFG1234567890"
    with caplog.at_level("INFO"):
        await _run(provider, image_url=secret_url)
    # The original URL must not appear in any record.
    for record in caplog.records:
        assert secret_url not in record.getMessage()


async def test_safe_log_payload_handles_api_keys(caplog) -> None:
    provider = _provider()
    with caplog.at_level("INFO"):
        await _run(provider, hint="call 13812345678")
    for record in caplog.records:
        assert "13812345678" not in record.getMessage()


# --------------------------------------------------------------- DB persistence


async def test_trace_persisted_when_user_home_given(db_session) -> None:
    provider = _provider()
    home_id = uuid.uuid4()
    user_id = uuid.uuid4()
    res = await vision_service.recognize_image(
        db_session,
        provider=provider,
        image_url="http://minio/x",
        home_id=home_id,
        user_id=user_id,
    )
    await db_session.commit()

    assert res.trace_id is not None
    row = (
        await db_session.execute(
            select(AgentTrace).where(AgentTrace.id == res.trace_id)
        )
    ).scalar_one()
    assert row.final_status == "success"
    assert row.home_id == home_id
    assert row.user_id == user_id
    assert isinstance(row.steps, list)
    assert len(row.steps) == 1
    # No image url in the persisted payload.
    serialized = json.dumps(row.steps, default=str)
    assert "minio/x" not in serialized
    # Prompt hash present.
    assert "prompt_hash" in row.steps[0]


async def test_trace_persisted_on_failure(db_session) -> None:
    provider = MockAIProvider(
        vision_side_effect=AIProviderAuthError("bad key")
    )
    home_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with pytest.raises(AIProviderAuthError):
        await vision_service.recognize_image(
            db_session,
            provider=provider,
            image_url="http://minio/x",
            home_id=home_id,
            user_id=user_id,
        )
    await db_session.commit()

    row = (
        await db_session.execute(
            select(AgentTrace).where(AgentTrace.home_id == home_id)
        )
    ).scalar_one()
    assert row.final_status == "error"
    assert "AIProviderAuthError" in (row.error or "")
    assert row.steps[0]["error"] == "AIProviderAuthError"
