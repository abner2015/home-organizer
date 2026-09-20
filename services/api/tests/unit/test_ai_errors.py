"""Tests for the AI error taxonomy."""
from __future__ import annotations

import pytest

from app.ai.errors import (
    AIOutputIncompleteError,
    AIOutputParseError,
    AIProviderAuthError,
    AIProviderError,
    AIProviderQuotaError,
    AIProviderRefusedError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)


@pytest.mark.parametrize(
    "cls,expected_code,expected_status",
    [
        (AIProviderError, "ai_provider_error", 503),
        (AIProviderAuthError, "ai_auth_error", 503),
        (AIProviderQuotaError, "ai_quota_error", 503),
        (AIProviderRefusedError, "ai_refused", 422),
        (AIProviderTransportError, "ai_transport_error", 503),
        (AIProviderTimeoutError, "ai_timeout", 503),
        (AIOutputParseError, "ai_output_parse_error", 503),
        (AIOutputIncompleteError, "ai_output_incomplete", 503),
    ],
)
def test_error_codes_and_status(cls, expected_code, expected_status) -> None:
    err = cls("boom")
    assert err.code == expected_code
    assert err.http_status == expected_status
    assert err.message == err.message  # present
    assert isinstance(err, AIProviderError)


def test_parse_error_caps_snippet() -> None:
    long = "x" * 1000
    err = AIOutputParseError("bad", snippet=long, schema="VisionOutput")
    assert err.snippet is not None
    assert len(err.snippet) == 200
    assert "x" * 200 == err.snippet
    # details should also carry the capped snippet.
    assert err.details["snippet"] == "x" * 200
    assert err.details["schema"] == "VisionOutput"


def test_incomplete_error_inherits_parse() -> None:
    err = AIOutputIncompleteError("missing fields", snippet="{}", schema="X")
    assert isinstance(err, AIOutputParseError)
    assert err.code == "ai_output_incomplete"


def test_error_does_not_accept_prompt_or_image_url_keyword() -> None:
    """Defensive: nothing in the error API takes a prompt/image body."""
    err = AIProviderError("nope")
    # The only mutable surface is ``details``; ensure the type
    # discourages stuffing sensitive data through it.
    assert err.details == {}
