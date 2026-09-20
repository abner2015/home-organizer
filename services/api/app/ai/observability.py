"""Observability helpers for the AI layer.

Three responsibilities:

1. **Token / time accounting** — every LLM call measures wall-clock
   duration; providers that surface usage give us a token count too.
2. **Prompt hashing** — we hash the rendered prompt (SHA-256) and store
   the hash, not the body, in ``AgentTrace``. The original prompt
   template lives in versioned markdown files (see ``app.agent.prompts``).
3. **Log scrubbing** — image URLs and obvious API keys MUST NOT end up
   in logs / traces. Use :func:`safe_log_payload` before emitting any
   user-derived data.

We keep these helpers tiny and dependency-free so they can be imported
from the providers, the service layer, and the tests alike.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

# Crude but effective: matches sk-/sk_live-/sk_test-/AIza/.../ghp_/... prefixes
# plus any 32+ char base64-ish blob preceded by "key=" or "apikey".
_API_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(?:api[_-]?key|secret)[=:\s]+[A-Za-z0-9_-]{16,}"),
)

# PII — phone numbers (loose), email, Chinese ID-card (loose).
# Keep patterns narrow to avoid false positives in Chinese item names.
_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b1[3-9]\d{9}\b"),  # Chinese mobile
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),  # email
    re.compile(r"\b\d{17}[\dXx]\b"),  # Chinese national ID
)


def hash_prompt(prompt: str) -> str:
    """Return a SHA-256 hex digest of ``prompt``.

    The hash is used to correlate an LLM response with the prompt template
    that produced it — without storing the rendered prompt itself.
    """
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def redact_api_keys(text: str) -> str:
    """Replace obvious API keys with ``[REDACTED]``."""
    for pat in _API_KEY_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def scrub_pii(text: str) -> str:
    """Strip obvious PII (mobile / email / Chinese ID)."""
    for pat in _PII_PATTERNS:
        text = pat.sub("[PII]", text)
    return text


def scrub_image_url(url: str) -> str:
    """Return a short opaque token for an image URL suitable for logs.

    Image URLs are signed credentials in disguise (MinIO presigned URLs
    include the access key + signature). Never log them whole. The token
    is ``sha256(url)[:12]`` — long enough to correlate events for the
    same upload, short enough that it's useless off the server.
    """
    return f"img_{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"


def safe_log_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``payload`` safe for structured logging.

    - String values are passed through :func:`redact_api_keys` and
      :func:`scrub_pii`.
    - Keys named ``image_url`` / ``prompt`` / ``api_key`` / ``url`` are
      replaced by their redacted counterparts.
    - Non-string values are kept as-is.
    """
    SENSITIVE_KEYS = {
        "image_url",
        "imageUrl",
        "image",
        "prompt",
        "raw_prompt",
        "api_key",
        "apiKey",
        "authorization",
        "url",
        "presigned_url",
    }
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in SENSITIVE_KEYS and isinstance(v, str):
            if k in {"api_key", "apiKey", "authorization"}:
                out[k] = "[REDACTED]"
            elif k == "prompt" or k == "raw_prompt":
                out[k] = f"sha256:{hash_prompt(v)[:16]}"
            else:
                out[k] = scrub_image_url(v)
        elif isinstance(v, str):
            out[k] = redact_api_keys(scrub_pii(v))
        else:
            out[k] = v
    return out


@dataclass(slots=True)
class CallMetrics:
    """Per-call metrics returned alongside any provider result.

    Providers populate what they can; services and tests can assert on
    this object directly.
    """

    duration_ms: int
    prompt_hash: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    raw_response_bytes: int | None = None
    parse_ok: bool = True
    parse_error: str | None = None


@contextmanager
def timed() -> Iterator[callable[[], int]]:
    """Context manager that returns a callable yielding elapsed ms.

    Usage::

        with timed() as get_ms:
            await provider.vision(...)
        metrics.duration_ms = get_ms()
    """
    start = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - start) * 1000)

    yield _elapsed_ms
