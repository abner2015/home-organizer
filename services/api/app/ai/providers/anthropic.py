"""Anthropic Claude vision provider.

Targets Anthropic's Messages API
(``https://api.anthropic.com/v1/messages``). Vision is supported via the
``image`` content block — we accept both remote URLs (Anthropic fetches
them) and ``data:`` URIs.

Structured output is achieved by asking the model to emit ONLY a JSON
object (validated client-side) rather than via tool use; tool use would
be a strict improvement but is left for a follow-up.

All of the project's observability / scrubbing invariants from
:mod:`app.ai.observability` apply: never log the API key, never log
image URLs, never log the raw prompt.
"""
from __future__ import annotations

import json
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


class AnthropicProvider:
    """Provider for Anthropic Claude's Messages API."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        vision_model: str | None = None,
        chat_model: str | None = None,
        default_timeout_s: float | None = None,
        api_version: str = "2023-06-01",
    ) -> None:
        self.api_key = api_key or settings.ai_api_key
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.vision_model = vision_model or settings.ai_model_vision
        self.chat_model = chat_model or settings.ai_model_recommend
        self.default_timeout_s = default_timeout_s or settings.ai_timeout_s
        self.api_version = api_version

    # ---------------------------------------------------------- public API

    async def vision(
        self,
        image_url: str,
        *,
        hint: str | None = None,
        timeout_s: float | None = None,
    ) -> VisionOutput:
        from app.agent.prompts import render

        prompt = render("vision", version=1, hint=(hint or ""))
        prompt_hash = hash_prompt(prompt)
        timeout = timeout_s or self.default_timeout_s

        system_text, user_text = self._split_prompt(prompt)
        schema_hint = VisionOutput.model_json_schema()
        full_user_text = (
            f"{user_text}\n\n"
            f"请严格按以下 JSON schema 输出（多余字段会被拒绝）：\n"
            f"{json.dumps(schema_hint, ensure_ascii=False)}"
        )
        image_block = self._image_block(image_url)

        body = {
            "model": self.vision_model,
            "max_tokens": 1024,
            "system": system_text,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": full_user_text},
                        image_block,
                    ],
                }
            ],
        }

        try:
            with timed() as elapsed:
                response = await self._post_messages(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Anthropic vision call timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(
                f"Anthropic vision call failed: {exc!s}"
            ) from exc

        self._raise_for_status(response)
        text = self._extract_text(response)
        data = self._parse_json(text)
        try:
            return VisionOutput.model_validate(data)
        except Exception as exc:
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
        timeout = timeout_s or self.default_timeout_s
        system_text, anthropic_messages = self._adapt_chat_messages(messages)
        body = {
            "model": self.chat_model,
            "max_tokens": 1024,
            "system": system_text,
            "messages": anthropic_messages,
        }
        try:
            response = await self._post_messages(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Anthropic chat timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(
                f"Anthropic chat failed: {exc!s}"
            ) from exc
        self._raise_for_status(response)
        return self._extract_text(response)

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        timeout_s: float | None = None,
    ) -> BaseModel:
        timeout = timeout_s or self.default_timeout_s
        schema_hint = schema.model_json_schema()
        body = {
            "model": self.chat_model,
            "max_tokens": 1024,
            "system": (
                "你是一个严格遵循 schema 的结构化输出助手。"
                "只输出一个 JSON 对象，不要任何额外文本。"
            ),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        f"请严格按以下 JSON schema 输出：\n"
                        f"{json.dumps(schema_hint, ensure_ascii=False)}"
                    ),
                }
            ],
        }
        try:
            response = await self._post_messages(body, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AIProviderTimeoutError(
                f"Anthropic structured-output timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIProviderTransportError(
                f"Anthropic structured-output failed: {exc!s}"
            ) from exc
        self._raise_for_status(response)
        text = self._extract_text(response)
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

        The Rank method has no ``prompt`` parameter in the Protocol
        (``docs/AI.md`` §2), so the provider builds the project's Rank prompt
        itself and delegates to :meth:`structured_output` for JSON-mode
        parsing + ``RankingOutput`` validation.
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

    @staticmethod
    def _split_prompt(prompt: str) -> tuple[str, str]:
        """Split a vision prompt body on the first ``---`` separator."""
        parts = prompt.split("\n---\n", 1)
        if len(parts) == 2:
            return parts[0].removeprefix("SYSTEM:\n").strip(), parts[1].strip()
        # No separator: everything is the user message.
        return "", prompt.strip()

    @staticmethod
    def _adapt_chat_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI-style chat messages to Anthropic format.

        Pulls out any leading ``system`` role; the rest pass through.
        """
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str):
                    system_parts.append(content)
                continue
            if role in ("user", "assistant"):
                out.append({"role": role, "content": content})
        return "\n\n".join(system_parts), out

    @staticmethod
    def _image_block(image_url: str) -> dict[str, Any]:
        """Translate ``image_url`` into an Anthropic image content block."""
        if image_url.startswith("data:"):
            # data:image/jpeg;base64,XXXX
            header, _, b64 = image_url.partition(",")
            media_type = header.split(":", 1)[1].split(";", 1)[0]
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64},
            }
        if image_url.startswith("http://") or image_url.startswith("https://"):
            return {"type": "image", "source": {"type": "url", "url": image_url}}
        raise AIProviderError("image_url must be http(s):// or data: URI")

    async def _post_messages(self, body: dict[str, Any], *, timeout: float) -> httpx.Response:
        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(url, json=body, headers=headers)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        code = response.status_code
        if code in (401, 403):
            raise AIProviderAuthError(f"Anthropic auth failed ({code})")
        if code == 429:
            raise AIProviderQuotaError(f"Anthropic quota exceeded ({code})")
        if 400 <= code < 500:
            raise AIProviderRefusedError(f"Anthropic refused request ({code})")
        raise AIProviderError(f"Anthropic returned {code}")

    @staticmethod
    def _extract_text(response: httpx.Response) -> str:
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AIOutputParseError(
                "Response body is not valid JSON",
                snippet=response.text[:200],
                schema="anthropic.messages",
            ) from exc
        try:
            blocks = data["content"]
        except (KeyError, TypeError) as exc:
            raise AIOutputParseError(
                "Unexpected Anthropic response shape",
                snippet=response.text[:200],
                schema="anthropic.messages",
            ) from exc
        chunks: list[str] = []
        for block in blocks:
            if block.get("type") == "text":
                chunks.append(block.get("text", ""))
        return "".join(chunks)

    @staticmethod
    def _parse_json(text: str) -> object:
        stripped = text.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            stripped = "\n".join(lines[1:])
            if stripped.endswith("```"):
                stripped = stripped[:-3]
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise AIOutputParseError(
                "Anthropic output is not valid JSON",
                snippet=text,
                schema="json",
            ) from exc


_ = (CallMetrics,)


__all__ = ["AnthropicProvider"]
