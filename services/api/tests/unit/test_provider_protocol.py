"""Tests for the AIProvider Protocol and VisionOutput schema."""
from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from app.ai.provider import AIProvider, VisionOutput
from app.ai.providers.mock import MockAIProvider


def test_vision_output_requires_name() -> None:
    with pytest.raises(ValidationError):
        VisionOutput.model_validate(
            {
                "category": "厨房",
                "subcategory": "杯具",
                "usage_scene": "饮用",
                "usage_frequency": "high",
                "size_class": "small",
                "fragility": "medium",
                "notes": "",
            }
        )


def test_vision_output_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VisionOutput.model_validate(
            {
                "name": "马克杯",
                "category": "厨房",
                "subcategory": "杯具",
                "usage_scene": "饮用",
                "usage_frequency": "high",
                "size_class": "small",
                "fragility": "medium",
                "notes": "",
                "extra_field": "nope",
            }
        )


def test_vision_output_rejects_wrong_enum() -> None:
    with pytest.raises(ValidationError):
        VisionOutput.model_validate(
            {
                "name": "x",
                "category": "厨房",
                "usage_frequency": "extremely_high",  # not in enum
                "size_class": "small",
                "fragility": "medium",
            }
        )


def test_vision_output_rejects_wrong_type() -> None:
    with pytest.raises(ValidationError):
        VisionOutput.model_validate(
            {
                "name": 123,  # must be str
                "category": "厨房",
                "size_class": "small",
                "fragility": "low",
                "usage_frequency": "low",
            }
        )


def test_vision_output_accepts_minimal_valid() -> None:
    out = VisionOutput.model_validate(
        {
            "name": "x",
            "category": "厨房",
            "size_class": "small",
            "fragility": "low",
            "usage_frequency": "low",
        }
    )
    assert out.name == "x"
    assert out.category == "厨房"
    # Defaults
    assert out.subcategory == ""
    assert out.usage_scene == ""
    assert out.notes == ""
    # Sensitivity fields were added with vision.v2.md; both are opt-in.
    assert out.is_sensitive is False
    assert out.needs_lock is False


def test_vision_output_tolerates_a_blank_category() -> None:
    """「无法对应时留空字符串」 is an instruction, not an error.

    ``vision.v2.md`` tells the model to leave ``category`` empty when nothing in
    the caller's real vocabulary fits — and ``build_home_context`` emits
    「（暂无，任何情况下都留空字符串）」 for a home with no storage at all. With
    ``min_length=1`` that obedient blank raised ``AIOutputIncompleteError``, so a
    brand-new user's first photo burned two futile retries and then failed.
    """
    out = VisionOutput.model_validate(
        {
            "name": "未知物品",
            "category": "",
            "size_class": "small",
            "fragility": "low",
            "usage_frequency": "low",
        }
    )
    assert out.category == ""
    # The field is omitted entirely by a provider that drops empty values.
    assert VisionOutput.model_validate({"name": "x"}).category == ""


def test_recognition_result_mirrors_the_blank_category() -> None:
    """`POST /items/recognize` validates this mirror, so it must relax too."""
    from app.schemas.recognition import RecognitionResult

    assert RecognitionResult.model_validate({"name": "x", "category": ""}).category == ""
    # `name` is still mandatory — a nameless item is useless to the user.
    with pytest.raises(ValidationError):
        RecognitionResult.model_validate({"category": "厨房"})


def test_vision_output_accepts_sensitivity_flags() -> None:
    out = VisionOutput.model_validate(
        {
            "name": "处方药",
            "category": "medicine",
            "size_class": "small",
            "fragility": "low",
            "usage_frequency": "low",
            "is_sensitive": True,
            "needs_lock": True,
        }
    )
    assert out.is_sensitive is True
    assert out.needs_lock is True


def test_item_inference_output_tolerates_explicit_nulls() -> None:
    """The infer prompt says "leave it out"; the model may answer ``null``.

    A non-optional field would turn a legitimate "I don't know" into an
    ``AIOutputParseError``, so every guessed field is nullable.
    """
    from app.ai.provider import ItemInferenceOutput

    out = ItemInferenceOutput.model_validate(
        {
            "name": "某种东西",
            "category": None,
            "subcategory": None,
            "description": None,
            "estimated_size": None,
        }
    )
    assert out.category is None
    assert out.estimated_size is None
    assert out.is_sensitive is False
    assert out.needs_lock is False


def test_item_inference_output_rejects_an_invented_size() -> None:
    from pydantic import ValidationError

    from app.ai.provider import ItemInferenceOutput

    with pytest.raises(ValidationError):
        ItemInferenceOutput.model_validate(
            {"name": "x", "estimated_size": "enormous"}
        )


def test_item_inference_output_rejects_extra_fields() -> None:
    from pydantic import ValidationError

    from app.ai.provider import ItemInferenceOutput

    with pytest.raises(ValidationError):
        ItemInferenceOutput.model_validate({"name": "x", "colour": "red"})


def test_ranking_output_accepts_json_parsed_uuid_strings() -> None:
    """The Rank schema must validate *JSON text*, where ids are strings.

    Regression: ``strict=True`` made ``CandidateSlot`` demand real ``UUID``
    instances, so every real-provider Rank reply failed validation with
    "Structured output missing required fields" and the agent could never
    answer. ``extra="forbid"`` stays for hygiene.
    """
    from app.ai.provider import RankingOutput

    payload = json.loads(
        '{"candidates": [{"slot_id": "f174224a-2179-4f55-87b2-fa1e35107868",'
        ' "confidence": 0.92, "reason": "带锁抽屉", "matched_rules": [],'
        ' "evidence_item_ids": ["540e9cd9-d7d7-4dfc-bda8-f0b39b1c73c9"]}]}'
    )
    out = RankingOutput.model_validate(payload)
    assert out.candidates[0].slot_id == uuid.UUID(
        "f174224a-2179-4f55-87b2-fa1e35107868"
    )
    assert out.candidates[0].evidence_item_ids == [
        uuid.UUID("540e9cd9-d7d7-4dfc-bda8-f0b39b1c73c9")
    ]


def test_ranking_output_rejects_extra_fields() -> None:
    from app.ai.provider import RankingOutput

    with pytest.raises(ValidationError):
        RankingOutput.model_validate(
            {
                "candidates": [
                    {
                        "slot_id": str(uuid.uuid4()),
                        "confidence": 0.5,
                        "reason": "x",
                        "bogus": 1,
                    }
                ]
            }
        )


def test_ranking_output_rejects_empty_candidates() -> None:
    from app.ai.provider import RankingOutput

    with pytest.raises(ValidationError):
        RankingOutput.model_validate({"candidates": []})


def test_mock_provider_implements_protocol() -> None:
    mock = MockAIProvider()
    # AIProvider is a runtime-checkable Protocol.
    assert isinstance(mock, AIProvider)
    assert mock.name == "mock"


def test_mock_provider_vision_returns_structured_output() -> None:
    import asyncio

    async def go() -> VisionOutput:
        return await MockAIProvider().vision("http://x/y.png")

    out = asyncio.run(go())
    assert out.name == "马克杯"
    assert out.category == "厨房"
    assert out.usage_frequency == "high"


def test_mock_provider_records_calls() -> None:
    import asyncio

    async def go() -> None:
        m = MockAIProvider()
        await m.vision("http://x/y.png", hint="a hint")
        await m.chat([{"role": "user", "content": "hi"}])
        assert m.call_count == 2
        assert m.recorded_calls[0][0] == "vision"
        assert m.recorded_calls[0][1]["hint"] == "a hint"
        assert m.recorded_calls[1][0] == "chat"

    asyncio.run(go())


def test_mock_provider_vision_raises_typed_error() -> None:
    import asyncio

    from app.ai.errors import AIProviderTimeoutError

    async def go() -> None:
        m = MockAIProvider(vision_side_effect=AIProviderTimeoutError("slow"))
        with pytest.raises(AIProviderTimeoutError):
            await m.vision("http://x/y.png")

    asyncio.run(go())


def test_mock_provider_structured_output_uses_schema() -> None:
    import asyncio

    from app.ai.provider import RankingOutput

    slot_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")

    async def go() -> RankingOutput:
        m = MockAIProvider(
            ranking_response={
                "candidates": [
                    {
                        "slot_id": slot_uuid,
                        "confidence": 0.8,
                        "reason": "first pick",
                        "matched_rules": [],
                        "evidence_item_ids": [],
                    }
                ]
            }
        )
        return await m.rank_candidates(
            item={"name": "x"},
            candidates=[{"slot_id": slot_uuid}],
            rules=[],
            preferences=[],
            history=[],
        )

    out = asyncio.run(go())
    assert len(out.candidates) == 1
    assert out.candidates[0].confidence == 0.8
    assert out.candidates[0].slot_id == slot_uuid
