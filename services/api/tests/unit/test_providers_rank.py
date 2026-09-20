"""Tests for the real providers' Rank step.

``rank_candidates`` used to raise ``NotImplementedError`` in both real
providers, which made every production recommendation fail. The Protocol
gives the method no prompt parameter (``docs/AI.md`` §2), so each provider
builds the Rank prompt itself and delegates the network call + schema
validation to its own ``structured_output`` — which is what these tests pin
(without respx: the transport layer is stubbed at the ``structured_output``
seam).
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.ai.provider import RankingOutput
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

pytestmark = pytest.mark.asyncio

SLOT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def _ranking_output() -> RankingOutput:
    return RankingOutput.model_validate(
        {
            "candidates": [
                {
                    "slot_id": SLOT_ID,
                    "confidence": 0.9,
                    "reason": "马克杯是常用餐具，厨房吊柜方便取用",
                    "matched_rules": [],
                    "evidence_item_ids": [],
                }
            ]
        }
    )


class _Recorder:
    """Stand-in for ``structured_output`` that records its call arguments."""

    def __init__(self, result: RankingOutput) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, prompt: str, schema: type[Any], *, timeout_s: float | None = None
    ) -> RankingOutput:
        self.calls.append({"prompt": prompt, "schema": schema, "timeout_s": timeout_s})
        return self.result


@pytest.fixture(params=["openai_compatible", "anthropic"])
def provider_and_recorder(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, _Recorder]:
    if request.param == "openai_compatible":
        provider: Any = OpenAICompatibleProvider(api_key="test-key")
    else:
        provider = AnthropicProvider(api_key="test-key")
    recorder = _Recorder(_ranking_output())
    monkeypatch.setattr(provider, "structured_output", recorder)
    return provider, recorder


async def test_rank_candidates_delegates_with_ranking_schema(
    provider_and_recorder: tuple[Any, _Recorder],
) -> None:
    provider, recorder = provider_and_recorder
    out = await provider.rank_candidates(
        item={"name": "马克杯", "category": "utensil"},
        candidates=[{"id": str(SLOT_ID), "full_path": "厨房/吊柜/第1层"}],
        rules=[],
        preferences=[],
        history=[],
    )
    assert isinstance(out, RankingOutput)
    assert out.candidates[0].slot_id == SLOT_ID
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["schema"] is RankingOutput


async def test_rank_candidates_builds_a_prompt_with_item_and_candidates(
    provider_and_recorder: tuple[Any, _Recorder],
) -> None:
    provider, recorder = provider_and_recorder
    await provider.rank_candidates(
        item={"name": "马克杯", "category": "utensil"},
        candidates=[{"id": str(SLOT_ID), "full_path": "厨房/吊柜/第1层"}],
        rules=[{"name": "厨房不放过期食品"}],
        preferences=[],
        history=[],
    )
    prompt = recorder.calls[0]["prompt"]
    assert "马克杯" in prompt
    assert "厨房/吊柜/第1层" in prompt
    assert "厨房不放过期食品" in prompt
    # Candidates must be inside the prompt, not the raw slot_id only.
    assert str(SLOT_ID) in prompt


async def test_rank_candidates_surfaces_last_failure_in_prompt(
    provider_and_recorder: tuple[Any, _Recorder],
) -> None:
    """Retry loop wiring: the verifier's failure text must reach the LLM."""
    provider, recorder = provider_and_recorder
    await provider.rank_candidates(
        item={"name": "马克杯", "category": "utensil"},
        candidates=[{"id": str(SLOT_ID), "full_path": "厨房/吊柜/第1层"}],
        rules=[],
        preferences=[],
        history=[],
        last_failure="该位置已满",
    )
    prompt = recorder.calls[0]["prompt"]
    assert "该位置已满" in prompt
    # With no failure the template's placeholder must still render.
    assert "{last_failure}" not in prompt


async def test_rank_candidates_passes_timeout_through(
    provider_and_recorder: tuple[Any, _Recorder],
) -> None:
    provider, recorder = provider_and_recorder
    await provider.rank_candidates(
        item={"name": "马克杯"},
        candidates=[{"id": str(SLOT_ID), "full_path": "厨房/吊柜/第1层"}],
        rules=[],
        preferences=[],
        history=[],
        timeout_s=7.5,
    )
    assert recorder.calls[0]["timeout_s"] == 7.5


async def test_rank_candidates_default_timeout_is_none(
    provider_and_recorder: tuple[Any, _Recorder],
) -> None:
    """``None`` lets ``structured_output`` apply the provider's default."""
    provider, recorder = provider_and_recorder
    await provider.rank_candidates(
        item={"name": "马克杯"},
        candidates=[{"id": str(SLOT_ID), "full_path": "厨房/吊柜/第1层"}],
        rules=[],
        preferences=[],
        history=[],
    )
    assert recorder.calls[0]["timeout_s"] is None
