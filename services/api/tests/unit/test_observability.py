"""Tests for app.ai.observability — scrubbing and metrics."""
from __future__ import annotations

import json
from dataclasses import asdict

from app.ai.observability import (
    CallMetrics,
    hash_prompt,
    redact_api_keys,
    safe_log_payload,
    scrub_image_url,
    scrub_pii,
    timed,
)


def test_hash_prompt_is_deterministic() -> None:
    assert hash_prompt("hello") == hash_prompt("hello")
    assert hash_prompt("hello") != hash_prompt("world")


def test_hash_prompt_returns_64_hex() -> None:
    h = hash_prompt("x")
    assert len(h) == 64
    int(h, 16)


def test_redact_api_keys_sk_live() -> None:
    text = "key=sk_live_abcdefghijklmnop1234567890abcd end"
    out = redact_api_keys(text)
    assert "sk_live_" not in out
    assert "[REDACTED]" in out


def test_redact_api_keys_openai() -> None:
    text = "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz1234567890"
    assert "sk-" in text
    out = redact_api_keys(text)
    assert "sk-" not in out
    assert "[REDACTED]" in out


def test_redact_api_keys_no_match() -> None:
    text = "just a normal sentence without keys"
    assert redact_api_keys(text) == text


def test_scrub_pii_chinese_mobile() -> None:
    out = scrub_pii("call me at 13812345678 thanks")
    assert "13812345678" not in out
    assert "[PII]" in out


def test_scrub_pii_email() -> None:
    out = scrub_pii("contact alice@example.com please")
    assert "alice@example.com" not in out


def test_scrub_image_url_is_opaque_and_short() -> None:
    url = "http://localhost:9000/foo/bar?signature=abc"
    token = scrub_image_url(url)
    assert url not in token
    assert token.startswith("img_")
    assert len(token) == 4 + 12


def test_safe_log_payload_redacts_image_url() -> None:
    out = safe_log_payload({"image_url": "http://minio/foo?X-Amz-Signature=secret"})
    assert "minio" not in out["image_url"]
    assert out["image_url"].startswith("img_")


def test_safe_log_payload_redacts_api_key() -> None:
    out = safe_log_payload({"api_key": "sk-abcdefghijklmnopqrstuvwxyz1234567890"})
    assert out["api_key"] == "[REDACTED]"


def test_safe_log_payload_hashes_prompt() -> None:
    out = safe_log_payload({"prompt": "long prompt body here"})
    assert out["prompt"].startswith("sha256:")
    assert "long prompt" not in out["prompt"]


def test_safe_log_payload_scrubs_pii_in_strings() -> None:
    out = safe_log_payload({"description": "call 13812345678"})
    assert "13812345678" not in out["description"]


def test_safe_log_payload_passes_through_non_strings() -> None:
    out = safe_log_payload({"count": 5, "flag": True, "data": [1, 2]})
    assert out == {"count": 5, "flag": True, "data": [1, 2]}


def test_timed_measures_elapsed_ms() -> None:
    with timed() as get_ms:
        elapsed_before = get_ms()
    assert elapsed_before >= 0


def test_call_metrics_dataclass() -> None:
    m = CallMetrics(
        duration_ms=10,
        prompt_hash="abc",
        parse_ok=False,
        parse_error="bad json",
    )
    # CallMetrics is a slots dataclass so it has no __dict__; use asdict.
    payload = json.dumps(asdict(m))
    assert "bad json" in payload
