"""Unit tests for the eval harness's mock DECIDE provider.

``CandidateAwareMockProvider`` exists because the scripted ``MockAIProvider``
replayed a fixed slot list, so the harness handed every case the same slots and
never let a retry make progress. These tests pin the replacement behaviour: take
proposals from the candidate list, follow the ranker's order, and drop the
previous lead on retry.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.evaluation.runner import CandidateAwareMockProvider
from app.verification.checks import check_reason_consistent
from app.verification.context import VerificationContext

pytestmark = pytest.mark.asyncio

ITEM: dict[str, Any] = {"name": "处方药", "category": "medicine"}


def _candidates(count: int) -> list[dict[str, Any]]:
    return [
        {"id": str(uuid.uuid4()), "full_path": f"主卧/柜子/层/S-{i}"} for i in range(count)
    ]


async def _rank(
    provider: CandidateAwareMockProvider,
    candidates: list[dict[str, Any]],
    *,
    last_failure: str | None = None,
):
    return await provider.rank_candidates(
        item=ITEM,
        candidates=candidates,
        rules=[],
        preferences=[],
        history=[],
        last_failure=last_failure,
    )


async def test_proposes_only_slots_from_the_candidate_list() -> None:
    candidates = _candidates(5)
    output = await _rank(CandidateAwareMockProvider(), candidates)
    allowed = {str(c["id"]) for c in candidates}
    assert {str(c.slot_id) for c in output.candidates} <= allowed


async def test_leads_with_the_ranker_top_candidate() -> None:
    candidates = _candidates(5)
    output = await _rank(CandidateAwareMockProvider(), candidates)
    assert str(output.candidates[0].slot_id) == candidates[0]["id"]


async def test_caps_the_number_of_proposals() -> None:
    output = await _rank(CandidateAwareMockProvider(), _candidates(10))
    assert len(output.candidates) == 3


async def test_confidence_decays_by_rank_but_stays_in_range() -> None:
    output = await _rank(CandidateAwareMockProvider(), _candidates(3))
    confidences = [c.confidence for c in output.candidates]
    assert confidences == sorted(confidences, reverse=True)
    assert all(0.0 <= c <= 1.0 for c in confidences)


async def test_retry_drops_the_previously_led_slot() -> None:
    """A retry must propose something different, or it can never converge."""
    provider = CandidateAwareMockProvider()
    candidates = _candidates(3)
    first = await _rank(provider, candidates)
    lead = str(first.candidates[0].slot_id)

    second = await _rank(provider, candidates, last_failure="slot rejected")

    assert str(second.candidates[0].slot_id) != lead
    assert str(second.candidates[0].slot_id) == candidates[1]["id"]


async def test_repeated_retries_walk_down_the_candidate_list() -> None:
    provider = CandidateAwareMockProvider()
    candidates = _candidates(3)
    leads = []
    for _ in range(3):
        output = await _rank(provider, candidates, last_failure="slot rejected")
        leads.append(str(output.candidates[0].slot_id))
    assert leads == [c["id"] for c in candidates]


async def test_exhausted_pool_falls_back_instead_of_emitting_nothing() -> None:
    provider = CandidateAwareMockProvider()
    candidates = _candidates(1)
    for _ in range(3):
        output = await _rank(provider, candidates, last_failure="slot rejected")
    # RankingOutput requires >= 1 candidate, so it re-proposes rather than crash.
    assert len(output.candidates) == 1


async def test_reason_satisfies_the_verifier_reason_check() -> None:
    candidates = _candidates(1)
    output = await _rank(CandidateAwareMockProvider(), candidates)
    picked = output.candidates[0]
    slot_id = picked.slot_id
    slot = {"id": str(slot_id), "full_path": "主卧/主卧衣柜/带锁抽屉/LK1", "reason": picked.reason}
    ctx = VerificationContext(
        home_id=uuid.uuid4(),
        known_slot_ids=frozenset({slot_id}),
        whitelist_slot_ids=frozenset({slot_id}),
        slots_by_id={},
        active_count={},
        item=ITEM,
        hard_rules=(),
        user_preferences=(),
    )
    assert check_reason_consistent(ctx, slot).passed is True
