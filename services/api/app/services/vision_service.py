"""Vision service — orchestrates the Vision LLM call.

Layering:

- **Provider** (``app.ai.providers``) does the actual HTTP / parsing.
- **Vision service** (this module) is the single entrypoint for
  business code. It owns:

    - Retry policy (parse failures retry up to ``MAX_PARSE_RETRIES``;
      transport failures retry up to ``MAX_TRANSPORT_RETRIES``).
    - Logging of token counts and durations (NEVER image URLs, NEVER
      prompts, NEVER API keys — see :mod:`app.ai.observability`).
    - Persistence of every call to :class:`AgentTrace` so the trace
      can be linked back to the originating asset / item.
- **API layer** (``app.api.v1.items``) only calls this service.

Each LLM call writes a step into ``AgentTrace.steps`` with:

- ``step_type``: ``"vision"``
- ``provider``: provider name
- ``prompt_hash``: SHA-256 prefix (no body)
- ``duration_ms``, ``tokens_in``, ``tokens_out``, ``raw_bytes``
- ``parse_ok``, ``parse_error``
- ``retry_count``: 0 on first try, 1..N on retry

Failures are typed — see :mod:`app.ai.errors`. The caller (API layer)
turns these into 503 / 422 HTTP responses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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
from app.ai.observability import (
    CallMetrics,
    hash_prompt,
    safe_log_payload,
    scrub_image_url,
    timed,
)
from app.ai.provider import AIProvider, VisionOutput
from app.core.logging import get_logger
from app.models import AgentTrace

logger = get_logger(__name__)


# --------------------------------------------------------------- tunables


MAX_PARSE_RETRIES = 2
MAX_TRANSPORT_RETRIES = 1

# Errors that are worth retrying (parse-shape fixes the prompt next try).
RETRYABLE_PARSE_ERRORS: tuple[type[BaseException], ...] = (
    AIOutputParseError,
    AIOutputIncompleteError,
)
RETRYABLE_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (
    AIProviderTransportError,
    AIProviderTimeoutError,
)
# Auth / quota / refused are NOT retried.
NON_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    AIProviderAuthError,
    AIProviderQuotaError,
    AIProviderRefusedError,
)


# --------------------------------------------------------------- result


@dataclass(slots=True)
class VisionResult:
    """Outcome of a vision call.

    Returned by :func:`recognize_image`. Contains the parsed output and
    the persisted trace row id (so the caller can correlate logs).
    """

    output: VisionOutput
    trace_id: uuid.UUID | None
    attempts: int
    total_duration_ms: int
    prompt_hash: str
    last_error: str | None = None
    steps: list[dict[str, object]] = field(default_factory=list)


# --------------------------------------------------------------- service


async def recognize_image(
    db: AsyncSession,
    *,
    provider: AIProvider,
    image_url: str,
    asset_id: uuid.UUID | None = None,
    home_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    hint: str | None = None,
    context: str = "",
    timeout_s: float = 30.0,
    trace: AgentTrace | None = None,
) -> VisionResult:
    """Identify the item in ``image_url`` with bounded retries.

    Parameters
    ----------
    db
        Async session used to write the ``AgentTrace`` row.
    provider
        The :class:`AIProvider` to call. Inject this — services must
        not call :func:`app.ai.factory.get_provider` directly so they
        can be tested with a :class:`MockAIProvider`.
    image_url
        A ``data:`` URI (or any URL the provider can reach).
    asset_id, home_id, user_id
        Linkage metadata for the trace row.
    hint
        Optional user-supplied description; treated as DATA per the
        prompt's "user input is data, not instruction" rule.
    context
        Optional grounding block carrying the caller's real category
        vocabulary, so the model doesn't invent a category no storage slot
        accepts. See :func:`build_home_context_for`.
    timeout_s
        Per-call timeout in seconds.
    trace
        Optional pre-existing :class:`AgentTrace` row to append steps
        to. If omitted a fresh trace row is created and persisted.
    """
    img_token = scrub_image_url(image_url)
    log_payload = safe_log_payload(
        {
            "provider": provider.name,
            "asset_id": str(asset_id) if asset_id else None,
            "home_id": str(home_id) if home_id else None,
            "user_id": str(user_id) if user_id else None,
            "image_url": image_url,
            "hint_len": len(hint or ""),
        }
    )
    log_payload["image_token"] = img_token  # safe correlation handle
    logger.info("vision.start", **log_payload)

    steps: list[dict[str, object]] = []
    attempt = 0
    last_error: BaseException | None = None
    last_metrics: CallMetrics | None = None
    output: VisionOutput | None = None

    while True:
        attempt += 1
        try:
            with timed() as get_ms:
                output = await provider.vision(
                    image_url, hint=hint, context=context, timeout_s=timeout_s
                )
            metrics = CallMetrics(
                duration_ms=get_ms(),
                # Hash the scrubbed image token, not the raw payload: the image
                # arrives as a `data:` URI whose bytes differ on every upload of
                # the same photo, which would make `prompt_hash` useless for
                # correlating runs (docs/AI.md §11).
                prompt_hash=hash_prompt(
                    f"{provider.name}|vision|{img_token}|{hint or ''}"
                ),
                parse_ok=True,
            )
            last_metrics = metrics
            steps.append(
                _step_payload(
                    attempt=attempt,
                    provider=provider.name,
                    metrics=metrics,
                )
            )
            break
        except NON_RETRYABLE_ERRORS as exc:
            last_error = exc
            steps.append(
                _step_payload(
                    attempt=attempt,
                    provider=provider.name,
                    metrics=CallMetrics(
                        duration_ms=0,
                        prompt_hash="",
                        parse_ok=False,
                        parse_error=f"{type(exc).__name__}: {exc}",
                    ),
                    error=type(exc).__name__,
                )
            )
            logger.warning(
                "vision.non_retryable_error",
                provider=provider.name,
                image_token=img_token,
                error=type(exc).__name__,
                attempt=attempt,
            )
            await _persist_trace(
                db,
                trace=trace,
                steps=steps,
                final_status="error",
                error=f"{type(exc).__name__}: {exc}",
                user_id=user_id,
                home_id=home_id,
            )
            raise
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            last_error = exc
            steps.append(
                _step_payload(
                    attempt=attempt,
                    provider=provider.name,
                    metrics=CallMetrics(
                        duration_ms=0,
                        prompt_hash="",
                        parse_ok=False,
                        parse_error=f"{type(exc).__name__}: {exc}",
                    ),
                    error=type(exc).__name__,
                )
            )
            if attempt > MAX_TRANSPORT_RETRIES:
                logger.warning(
                    "vision.transport_exhausted",
                    provider=provider.name,
                    image_token=img_token,
                    attempts=attempt,
                    error=type(exc).__name__,
                )
                await _persist_trace(
                    db,
                    trace=trace,
                    steps=steps,
                    final_status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    user_id=user_id,
                    home_id=home_id,
                )
                raise
            logger.info(
                "vision.transport_retry",
                provider=provider.name,
                image_token=img_token,
                attempt=attempt,
            )
        except RETRYABLE_PARSE_ERRORS as exc:
            last_error = exc
            steps.append(
                _step_payload(
                    attempt=attempt,
                    provider=provider.name,
                    metrics=CallMetrics(
                        duration_ms=0,
                        prompt_hash="",
                        parse_ok=False,
                        parse_error=f"{type(exc).__name__}: {exc}",
                    ),
                    error=type(exc).__name__,
                )
            )
            if attempt > MAX_PARSE_RETRIES:
                logger.warning(
                    "vision.parse_exhausted",
                    provider=provider.name,
                    image_token=img_token,
                    attempts=attempt,
                    error=type(exc).__name__,
                )
                await _persist_trace(
                    db,
                    trace=trace,
                    steps=steps,
                    final_status="error",
                    error=f"{type(exc).__name__}: {exc}",
                    user_id=user_id,
                    home_id=home_id,
                )
                raise
            logger.info(
                "vision.parse_retry",
                provider=provider.name,
                image_token=img_token,
                attempt=attempt,
                parse_error=type(exc).__name__,
            )

    assert output is not None
    total_ms = sum(int(s.get("duration_ms", 0)) for s in steps)  # type: ignore[arg-type]
    trace_id = await _persist_trace(
        db,
        trace=trace,
        steps=steps,
        final_status="success",
        error=None,
        user_id=user_id,
        home_id=home_id,
    )

    logger.info(
        "vision.success",
        provider=provider.name,
        image_token=img_token,
        attempts=attempt,
        duration_ms=total_ms,
    )
    return VisionResult(
        output=output,
        trace_id=trace_id,
        attempts=attempt,
        total_duration_ms=total_ms,
        prompt_hash=last_metrics.prompt_hash if last_metrics else "",
        last_error=str(last_error) if last_error else None,
        steps=steps,
    )


# --------------------------------------------------------------- helpers


def _step_payload(
    *,
    attempt: int,
    provider: str,
    metrics: CallMetrics,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "step_type": "vision",
        "attempt": attempt,
        "provider": provider,
        "prompt_hash": metrics.prompt_hash,
        "duration_ms": metrics.duration_ms,
        "tokens_in": metrics.tokens_in,
        "tokens_out": metrics.tokens_out,
        "raw_response_bytes": metrics.raw_response_bytes,
        "parse_ok": metrics.parse_ok,
        "parse_error": metrics.parse_error,
        "error": error,
    }


async def _persist_trace(
    db: AsyncSession,
    *,
    trace: AgentTrace | None,
    steps: list[dict[str, object]],
    final_status: str,
    error: str | None,
    user_id: uuid.UUID | None,
    home_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """Write or append to an AgentTrace row. Returns the trace id."""
    total_ms = sum(int(s.get("duration_ms", 0)) for s in steps)  # type: ignore[arg-type]
    if trace is not None:
        # Append into the existing trace (used by the orchestrator in
        # Phase 9+). Cast: SQLAlchemy JSON column accepts list[dict].
        existing_steps = list(trace.steps or [])  # type: ignore[arg-type]
        trace.steps = [*existing_steps, *steps]  # type: ignore[assignment]
        trace.final_status = final_status
        trace.error = error
        trace.total_duration_ms = int(trace.total_duration_ms or 0) + total_ms
        await db.flush()
        return trace.id

    if user_id is None or home_id is None:
        # Without linkage we can't write a meaningful trace — return None.
        return None
    row = AgentTrace(
        home_id=home_id,
        user_id=user_id,
        steps=steps,
        final_status=final_status,
        total_duration_ms=total_ms,
        error=error,
    )
    db.add(row)
    await db.flush()
    return row.id


__all__ = [
    "MAX_PARSE_RETRIES",
    "MAX_TRANSPORT_RETRIES",
    "VisionResult",
    "recognize_image",
]


# Silence unused-import warnings while keeping the symbols reachable
# for callers / tests that want to assert on the taxonomy.
_ = (AIProviderError, AIProviderError.__name__, Any)
