"""AI layer error taxonomy.

Every failure that originates inside an :class:`AIProvider` implementation
MUST be classified into one of the exceptions below. The global exception
handler (and the vision service) switch on these types to decide on
retries, status codes, and user-facing messages.

Strict invariants:

- **Never** put a prompt body, an image URL, or an API key in ``args`` /
  ``details`` of any of these exceptions. They are user-visible (via 5xx
  error bodies) and end up in logs.
- ``AIOutputParseError`` may include a short ``snippet`` (≤ 200 chars) of
  the raw LLM response for debugging — but never the prompt.
"""
from __future__ import annotations

from typing import Any

from app.core.exceptions import AppError


class AIProviderError(AppError):
    """Base for all AI provider failures.

    Provider implementations raise one of the subclasses below. The global
    handler returns 503 to the client for any unrecoverable subclass.
    """

    code = "ai_provider_error"
    http_status = 503
    message = "AI provider error"


class AIProviderAuthError(AIProviderError):
    """401 / 403 from the provider — API key invalid / unauthorized.

    Always immediate-fail (no retry) and alertable. Surface as 503 to the
    client (NOT 401 — that's about the *user* not the *operator*).
    """

    code = "ai_auth_error"
    http_status = 503
    message = "AI provider authentication failed"


class AIProviderQuotaError(AIProviderError):
    """429 / quota exceeded — back off and surface as 503."""

    code = "ai_quota_error"
    http_status = 503
    message = "AI provider quota exceeded"


class AIProviderRefusedError(AIProviderError):
    """The provider refused the request (content policy / safety)."""

    code = "ai_refused"
    http_status = 422
    message = "AI provider refused the request"


class AIProviderTransportError(AIProviderError):
    """Network / DNS / connection error — typically transient.

    The vision service may retry this once before giving up.
    """

    code = "ai_transport_error"
    http_status = 503
    message = "AI provider is unreachable"


class AIProviderTimeoutError(AIProviderError):
    """The provider didn't respond within ``timeout_s``."""

    code = "ai_timeout"
    http_status = 503
    message = "AI provider timed out"


class AIOutputParseError(AIProviderError):
    """The LLM responded but the output wasn't valid JSON / didn't match
    the requested Pydantic schema.

    The vision service retries up to 2 times with progressively stricter
    prompt hints before bubbling up.
    """

    code = "ai_output_parse_error"
    http_status = 503
    message = "AI output could not be parsed"

    def __init__(
        self,
        message: str | None = None,
        *,
        snippet: str | None = None,
        schema: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details or {})
        if snippet:
            # Hard cap so a misbehaving provider can't blow up the logs.
            merged["snippet"] = snippet[:200]
        if schema:
            merged["schema"] = schema
        super().__init__(message, details=merged or None)
        # Stash on the instance for callers that want structured access
        # without going through ``details``.
        self.snippet = snippet[:200] if snippet else None
        self.schema = schema


class AIOutputIncompleteError(AIOutputParseError):
    """JSON parsed but required fields are missing / null.

    Same retry policy as :class:`AIOutputParseError`. The subclass exists
    so callers (and observability) can distinguish "bad JSON" from
    "JSON but wrong shape".
    """

    code = "ai_output_incomplete"
    message = "AI output missing required fields"
