"""Mock AIProvider for tests and local development.

Behavior is driven by per-instance configuration:

- ``vision_response`` / ``structured_output_response``: a dict that will
  be parsed into the requested schema; ``None`` to fall through to the
  scripted behavior.
- ``vision_side_effect``: an exception to raise instead of returning.
- ``call_count``: how many times any method has been invoked.
- ``recorded_calls``: list of ``(method_name, kwargs)`` for assertions.

This provider is the single source of truth for "what does the LLM do
under controlled test conditions" — every unit / integration test that
exercises the vision service or the recognition endpoint depends on it.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from pydantic import BaseModel

from app.ai.errors import (
    AIOutputParseError,
    AIProviderAuthError,
    AIProviderError,
    AIProviderQuotaError,
    AIProviderTimeoutError,
    AIProviderTransportError,
)
from app.ai.observability import CallMetrics, hash_prompt, timed
from app.ai.provider import RankingOutput, VisionOutput


class MockAIProvider:
    """In-memory AIProvider with fully programmable responses."""

    name = "mock"

    def __init__(
        self,
        *,
        vision_response: dict[str, Any] | None = None,
        vision_raw_text: str | None = None,
        vision_side_effect: BaseException | None = None,
        # Empty by default: the search service treats "the model said nothing"
        # as "no scripted phrasing" and falls back to its deterministic draft,
        # so an unscripted mock exercises the degraded path rather than
        # injecting placeholder text into a user-visible answer.
        chat_response: str = "",
        structured_output_response: dict[str, Any] | None = None,
        # Phase 6: when set, each structured_output() call pops the next entry;
        # may be a dict (parsed against the requested schema) or a BaseException
        # (raised). Mirrors the ``ranking_responses`` retry pattern.
        structured_output_responses: list[dict[str, Any] | BaseException] | None = None,
        ranking_response: dict[str, Any] | None = None,
        # Phase 8: when set, each rank_candidates() call pops the next entry;
        # may be a dict (parsed as RankingOutput) or a BaseException (raised).
        ranking_responses: list[dict[str, Any] | BaseException] | None = None,
        latency_ms: int = 0,
    ) -> None:
        self.vision_response = vision_response or {
            "name": "马克杯",
            "category": "厨房",
            "subcategory": "杯具",
            "usage_scene": "饮用",
            "usage_frequency": "high",
            "size_class": "small",
            "fragility": "medium",
            "notes": "陶瓷材质",
        }
        self.vision_raw_text = vision_raw_text
        self.vision_side_effect = vision_side_effect
        self.chat_response = chat_response
        self.structured_output_response = structured_output_response
        self.structured_output_responses = structured_output_responses
        self.ranking_response = ranking_response
        self.ranking_responses = ranking_responses
        self.latency_ms = latency_ms

        self.call_count = 0
        self.recorded_calls: list[tuple[str, dict[str, Any]]] = []

    # ----------------------------------------------------------------- helpers

    async def _maybe_sleep(self) -> None:
        if self.latency_ms > 0:
            await asyncio.sleep(self.latency_ms / 1000.0)

    @staticmethod
    def _metrics(*, parse_ok: bool, error: str | None, prompt: str) -> CallMetrics:
        return CallMetrics(
            duration_ms=0,
            prompt_hash=hash_prompt(prompt),
            parse_ok=parse_ok,
            parse_error=error,
        )

    # ----------------------------------------------------------------- AIProvider

    async def vision(
        self,
        image_url: str,
        *,
        hint: str | None = None,
        context: str = "",
        timeout_s: float = 30.0,
    ) -> VisionOutput:
        self.call_count += 1
        self.recorded_calls.append(
            ("vision", {"image_url": image_url, "hint": hint, "context": context})
        )
        await self._maybe_sleep()
        if self.vision_side_effect is not None:
            raise self.vision_side_effect

        if self.vision_raw_text is not None:
            # Force a parse path: try to parse vision_raw_text as JSON,
            # then validate as VisionOutput.
            try:
                data = json.loads(self.vision_raw_text)
            except json.JSONDecodeError as exc:
                raise AIOutputParseError(
                    "Vision response is not valid JSON",
                    snippet=self.vision_raw_text,
                    schema="VisionOutput",
                ) from exc
            try:
                return VisionOutput.model_validate(data)
            except Exception as exc:  # ValidationError etc.
                raise AIOutputParseError(
                    "Vision response did not match schema",
                    snippet=self.vision_raw_text,
                    schema="VisionOutput",
                ) from exc

        return VisionOutput.model_validate(self.vision_response)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout_s: float = 30.0,
    ) -> str:
        self.call_count += 1
        self.recorded_calls.append(("chat", {"messages": messages}))
        await self._maybe_sleep()
        return self.chat_response

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        timeout_s: float = 30.0,
    ) -> BaseModel:
        self.call_count += 1
        self.recorded_calls.append(
            ("structured_output", {"prompt": prompt, "schema": schema.__name__})
        )
        await self._maybe_sleep()

        # Phase 6 multi-call retry support: pop next scripted response.
        if self.structured_output_responses is not None:
            if not self.structured_output_responses:
                raise RuntimeError(
                    "MockAIProvider.structured_output_responses exhausted; no scripted reply"
                )
            nxt = self.structured_output_responses.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            try:
                return schema.model_validate(nxt)
            except Exception as exc:  # ValidationError / pydantic errors
                # Match real-provider contract: malformed payloads raise
                # AIOutputParseError so callers can distinguish "LLM
                # couldn't be reached" (transport / auth / quota) from
                # "LLM responded but garbage" (parse).
                raise AIOutputParseError(
                    "Structured output did not match schema",
                    snippet=str(nxt)[:200],
                    schema=schema.__name__,
                ) from exc

        if self.structured_output_response is None:
            raise RuntimeError("Mock structured_output_response not set")
        try:
            return schema.model_validate(self.structured_output_response)
        except Exception as exc:  # ValidationError / pydantic errors
            raise AIOutputParseError(
                "Structured output did not match schema",
                snippet=str(self.structured_output_response)[:200],
                schema=schema.__name__,
            ) from exc

    async def rank_candidates(
        self,
        *,
        item: dict[str, Any],
        candidates: list[dict[str, Any]],
        rules: list[dict[str, Any]],
        preferences: list[dict[str, Any]],
        history: list[dict[str, Any]],
        last_failure: str | None = None,
        timeout_s: float = 30.0,
    ) -> RankingOutput:
        self.call_count += 1
        self.recorded_calls.append(
            (
                "rank_candidates",
                {
                    "item": item,
                    "last_failure": last_failure,
                    "candidate_ids": [c.get("slot_id") or c.get("id") for c in candidates],
                },
            )
        )
        await self._maybe_sleep()

        def _coerce(data: dict[str, Any]) -> dict[str, Any]:
            """Pydantic ``strict=True`` rejects string→UUID coercion; normalise first."""
            out = dict(data)
            cands = []
            for c in out.get("candidates", []):
                cc = dict(c)
                sid = cc.get("slot_id")
                if isinstance(sid, str):
                    cc["slot_id"] = uuid.UUID(sid)
                cands.append(cc)
            out["candidates"] = cands
            return out

        # Phase 8 multi-call retry support: pop next scripted response.
        if self.ranking_responses is not None:
            if not self.ranking_responses:
                raise RuntimeError(
                    "MockAIProvider.ranking_responses exhausted; no scripted reply"
                )
            nxt = self.ranking_responses.pop(0)
            if isinstance(nxt, BaseException):
                raise nxt
            return RankingOutput.model_validate(_coerce(nxt))

        if self.ranking_response is not None:
            return RankingOutput.model_validate(_coerce(self.ranking_response))
        # Default: pick the first candidate with a synthetic reason.
        first = candidates[0]
        slot_id = first.get("slot_id") or first.get("id") or str(uuid.uuid4())
        slot_uuid = slot_id if isinstance(slot_id, uuid.UUID) else uuid.UUID(str(slot_id))
        return RankingOutput(
            candidates=[
                {
                    "slot_id": slot_uuid,
                    "confidence": 0.9,
                    "reason": "默认 mock 排序",
                    "matched_rules": [],
                    "evidence_item_ids": [],
                }
            ]
        )


# ----------------------------------------------------------------- scripted errors


def make_transient_error() -> AIProviderTransportError:
    return AIProviderTransportError("connection reset by peer")


def make_timeout_error() -> AIProviderTimeoutError:
    return AIProviderTimeoutError("read timed out after 30s")


def make_auth_error() -> AIProviderAuthError:
    return AIProviderAuthError("invalid api key")


def make_quota_error() -> AIProviderQuotaError:
    return AIProviderQuotaError("rate limit exceeded")


def make_unknown_error() -> AIProviderError:
    return AIProviderError("unknown failure")


# ----------------------------------------------------------------- helpers for tests


__all__ = [
    "MockAIProvider",
    "make_auth_error",
    "make_quota_error",
    "make_timeout_error",
    "make_transient_error",
    "make_unknown_error",
]


# Silence unused-import warning while keeping the helper symbols
# importable from tests that just want the canned exceptions.
_ = (timed,)
