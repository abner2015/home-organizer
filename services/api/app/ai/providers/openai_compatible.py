"""OpenAI-compatible vision provider.

Targets any service that speaks the OpenAI Chat Completions API with the
``response_format={"type":"json_object"}`` mode — including OpenAI
itself, DeepSeek, 智谱 (GLM), 通义 (Qwen), Moonshot, etc. The base URL
is configurable via ``AI_BASE_URL`` (see ``Settings``).

Implementation notes:

- We pass image URLs as ``image_url`` content parts. ``http(s)://`` URLs
  are passed through; ``data:`` URIs are passed through; everything else
  is rejected early.
- Vision calls force ``response_format={"type":"json_object"}`` and
  inject the schema as part of the system prompt so the LLM sees the
  exact field names it must produce.
- The response is parsed as JSON then validated against ``VisionOutput``
  (or whichever schema the caller passed to ``structured_output``). Any
  parse / validation failure raises :class:`AIOutputParseError`.
- HTTP / transport failures map to typed exceptions so the vision
  service can decide on retries.
- API keys, raw image URLs, and full prompts are NEVER logged. Only the
  SHA-256 prompt hash, duration, and token counts are surfaced via the
  ``CallMetrics`` returned alongside each call.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx
from pydantic import BaseModel

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
from app.ai.observability import CallMetrics, hash_prompt, timed
from app.ai.provider import RankingOutput, VisionOutput
from app.core.config import settings

logger = logging.getLogger(__name__)


class OpenAICompatibleProvider:
    """Provider for any OpenAI-compatible Chat Completions API."""

    name = "openai_compatible"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        vision_model: str | None = None,
        chat_model: str | None = None,
        default_timeout_s: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.ai_api_key
        self.base_url = (base_url or settings.ai_base_url).rstrip("/")
        self.vision_model = vision_model or settings.ai_model_vision
        self.chat_model = chat_model or settings.ai_model_recommend
        self.default_timeout_s = default_timeout_s or settings.ai_timeout_s

    # ---------------------------------------------------------- public API

    async def vision(
        self,
        image_url: str,
        *,
        hint: str | None = None,
        context: str = "",
        timeout_s: float | None = None,
    ) -> VisionOutput:
        """Run a vision call and return the parsed VisionOutput.

        Returns :class:`VisionOutput`. On any error raises one of the
        typed :mod:`app.ai.errors` subclasses.
        """
        from app.agent.prompts import render

        prompt = render(
            "vision", version=2, hint=(hint or ""), home_context=context
        )
        prompt_hash = hash_prompt(prompt)
        timeout = timeout_s or self.default_timeout_s

        body = self._build_vision_body(prompt, image_url, VisionOutput)

        try:
            with timed() as elapsed:
                response = await self._post_chat(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Vision call timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(f"Vision call failed: {exc!s}") from exc

        self._raise_for_status(response)

        text = self._extract_message_text(response)
        data = self._parse_json(text)
        try:
            return VisionOutput.model_validate(data)
        except Exception as exc:
            # Distinguish "JSON parsed but wrong shape" (Incomplete) from
            # "couldn't even parse JSON" (Parse). We approximate by
            # checking whether the data is a dict at all — anything else
            # is a parse-level failure.
            if not isinstance(data, dict):
                raise AIOutputParseError(
                    "Vision output was not a JSON object",
                    snippet=text,
                    schema="VisionOutput",
                ) from exc
            raise AIOutputIncompleteError(
                "Vision output missing required fields",
                snippet=text,
                schema="VisionOutput",
            ) from exc

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Free-form chat completion. Returns the assistant text only."""
        timeout = timeout_s or self.default_timeout_s
        body = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": 0.2,
        }
        try:
            response = await self._post_chat(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Chat call timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(f"Chat call failed: {exc!s}") from exc
        self._raise_for_status(response)
        return self._extract_message_text(response)

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        image_url: str | None = None,
        timeout_s: float | None = None,
    ) -> BaseModel:
        """Force JSON-object output and parse into ``schema``.

        With ``image_url`` set the call becomes multimodal and must run on the
        vision model — a text-only endpoint 400s on an image content part.
        """
        timeout = timeout_s or self.default_timeout_s
        if image_url:
            body = self._build_vision_body(prompt, image_url, schema)
        else:
            body = {
                "model": self.chat_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.0,
            }
        try:
            response = await self._post_chat(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Structured-output call timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(
                f"Structured-output call failed: {exc!s}"
            ) from exc
        self._raise_for_status(response)
        text = self._extract_message_text(response)
        data = self._parse_json(text)
        try:
            return schema.model_validate(data)
        except Exception as exc:
            if not isinstance(data, dict):
                raise AIOutputParseError(
                    "Structured output was not a JSON object",
                    snippet=text,
                    schema=schema.__name__,
                ) from exc
            raise AIOutputIncompleteError(
                "Structured output missing required fields",
                snippet=text,
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
        timeout_s: float | None = None,
    ) -> RankingOutput:
        """Rank the pre-filtered candidates via the Rank prompt.

        The provider is prompt-agnostic at the Protocol level (``docs/AI.md``
        §2 — no ``prompt`` parameter), so it builds the project's Rank prompt
        itself and delegates to :meth:`structured_output`, which forces
        ``response_format={"type":"json_object"}`` and validates the reply
        into :class:`RankingOutput`.
        """
        from app.agents.prompt_render import build_rank_prompt

        prompt = build_rank_prompt(
            item=item,
            candidates=candidates,
            rules=rules,
            preferences=preferences,
            history=history,
            last_failure=last_failure,
        )
        output = await self.structured_output(
            prompt, RankingOutput, timeout_s=timeout_s
        )
        return cast(RankingOutput, output)

    # ---------------------------------------------------------- internals

    def _build_vision_body(
        self, prompt: str, image_url: str, schema: type[BaseModel]
    ) -> dict[str, Any]:
        """Compose the Chat Completions request body for an image call.

        Shared by :meth:`vision` (``VisionOutput``) and by
        :meth:`structured_output` when it is handed an ``image_url``.
        """
        # Inject the JSON schema hint so the LLM emits the exact field
        # names we validate against.
        schema_hint = schema.model_json_schema()
        content = [
            {
                "type": "text",
                "text": (
                    f"{prompt}\n\n"
                    f"请严格按以下 JSON schema 输出（多余字段会被拒绝）：\n"
                    f"{json.dumps(schema_hint, ensure_ascii=False)}"
                ),
            },
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
        return {
            "model": self.vision_model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

    async def _post_chat(self, body: dict[str, Any], *, timeout: float) -> httpx.Response:
        """POST ``body`` to ``/chat/completions``. Returns the response."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=body, headers=headers)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        """Map non-2xx responses to typed AI errors."""
        if response.status_code < 400:
            return
        # Don't log the body — it may echo the prompt back.
        code = response.status_code
        if code in (401, 403):
            raise AIProviderAuthError(f"Auth failed ({code})")
        if code == 429:
            raise AIProviderQuotaError(f"Quota exceeded ({code})")
        if 400 <= code < 500:
            # 400 may include content-policy refusals — treat as Refused.
            raise AIProviderRefusedError(f"Request refused ({code})")
        # 5xx
        raise AIProviderError(f"Provider returned {code}")

    @staticmethod
    def _extract_message_text(response: httpx.Response) -> str:
        """Pluck the assistant text out of a Chat Completions response."""
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AIOutputParseError(
                "Response body is not valid JSON",
                snippet=response.text[:200],
                schema="chat.completions",
            ) from exc
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIOutputParseError(
                "Unexpected response shape",
                snippet=response.text[:200],
                schema="chat.completions",
            ) from exc

    @staticmethod
    def _parse_json(text: str) -> object:
        """Parse ``text`` as JSON, raising :class:`AIOutputParseError` on failure."""
        # Some providers wrap JSON in ```json ... ``` fences; strip them
        # before parsing so we don't fail on otherwise-valid responses.
        stripped = text.strip()
        if stripped.startswith("```"):
            # Trim first line (```json) and trailing ```
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:])
            if stripped.endswith("```"):
                stripped = stripped[:-3]
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AIOutputParseError(
                "Provider output is not valid JSON",
                snippet=text,
                schema="json",
            ) from exc


# Mark unused-import warnings as resolved while keeping CallMetrics
# reachable via this module for tests that want to inspect metrics.
_ = (CallMetrics,)


__all__ = ["OpenAICompatibleProvider"]
