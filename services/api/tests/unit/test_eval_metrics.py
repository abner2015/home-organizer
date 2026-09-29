"""Unit tests for the three new ranking-quality metrics (P2.1).

Covers ``mrr_score``, ``top1_accuracy``, and ``pre_filter_top1`` — see
``app.evaluation.metrics`` and ``docs/EVALUATION.md`` §3.4 / §3.5. The
metrics score the deterministic ranker in isolation (not the LLM's
``rank_candidates`` pick), so the test cases use ``top_slot_tags`` directly
without driving the full pipeline.
"""
from __future__ import annotations

from app.evaluation.metrics import (
    MetricReport,
    compute_metrics,
    mrr_score,
    pre_filter_top1,
    top1_accuracy,
)
from app.evaluation.runner import CaseResult


def _case(
    *,
    case_id: str,
    top_slot_tags: list[str],
    expected_slots: list[str],
    forbidden_slots: list[str] | None = None,
    pre_filter_count: int = 5,
    chosen_slot_tag: str | None = None,
) -> CaseResult:
    """Build a CaseResult with the bare minimum needed by the new metrics."""
    return CaseResult(
        case_id=case_id,
        category="test",
        expected_slots=expected_slots,
        forbidden_slots=forbidden_slots or [],
        chosen_slot_id=None,
        chosen_slot_tag=chosen_slot_tag or (top_slot_tags[0] if top_slot_tags else None),
        top_slot_ids=[],
        top_slot_tags=top_slot_tags,
        state="answer",
        retries_used=0,
        verifier_passed=True,
        item_recognition_correct=None,
        error=None,
        duration_ms=0,
        pre_filter_count=pre_filter_count,
    )


def test_mrr_zero_when_no_expected_match() -> None:
    results = [_case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["c"])]
    assert mrr_score(results) == 0.0


def test_mrr_one_when_expected_at_top() -> None:
    results = [_case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["a"])]
    assert mrr_score(results) == 100.0


def test_mrr_two_thirds_when_expected_at_rank_3() -> None:
    # reciprocal of 3 is 1/3 → 33.33%.
    results = [_case(case_id="c1", top_slot_tags=["a", "b", "c"], expected_slots=["c"])]
    assert mrr_score(results) == 33.33


def test_mrr_mean_across_cases() -> None:
    results = [
        _case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["a"]),  # rr=1
        _case(case_id="c2", top_slot_tags=["x", "y"], expected_slots=["a"]),  # rr=0
        _case(case_id="c3", top_slot_tags=["p", "q", "a"], expected_slots=["a"]),  # rr=1/3
    ]
    # mean of (1 + 0 + 1/3) = 4/9 ≈ 0.4444 → 44.44%.
    assert mrr_score(results) == 44.44


def test_top1_accuracy_matches_first_tag_in_expected() -> None:
    results = [_case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["a"])]
    assert top1_accuracy(results) == 100.0


def test_top1_accuracy_zero_when_first_not_in_expected() -> None:
    results = [_case(case_id="c1", top_slot_tags=["x", "y"], expected_slots=["a"])]
    assert top1_accuracy(results) == 0.0


def test_top1_accuracy_partial() -> None:
    results = [
        _case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["a"]),  # hit
        _case(case_id="c2", top_slot_tags=["x", "y"], expected_slots=["a"]),  # miss
    ]
    assert top1_accuracy(results) == 50.0


def test_top1_accuracy_handles_empty_top_slot_tags() -> None:
    # No candidates at all → top-1 is undefined → contributes a miss.
    results = [_case(case_id="c1", top_slot_tags=[], expected_slots=["a"])]
    assert top1_accuracy(results) == 0.0


def test_pre_filter_top1_returns_one_when_pre_filter_populated_and_top_match() -> None:
    results = [_case(case_id="c1", top_slot_tags=["a"], expected_slots=["a"], pre_filter_count=3)]
    assert pre_filter_top1(results) == 100.0


def test_pre_filter_top1_returns_zero_when_pre_filter_populated_and_no_match() -> None:
    results = [_case(case_id="c1", top_slot_tags=["x"], expected_slots=["a"], pre_filter_count=3)]
    assert pre_filter_top1(results) == 0.0


def test_pre_filter_top1_excludes_cases_with_zero_pre_filter_count() -> None:
    # ``pre_filter_count == 0`` means the filter killed the case — that is a
    # separate concern (covered by Hard Constraint Violation). Exclude from
    # the denominator so a filter-killed case doesn't drag the metric to 0.
    results = [
        _case(case_id="killed", top_slot_tags=[], expected_slots=["a"], pre_filter_count=0),
        _case(case_id="alive", top_slot_tags=["a"], expected_slots=["a"], pre_filter_count=3),
    ]
    # Only the "alive" case is eligible → 100% of eligible.
    assert pre_filter_top1(results) == 100.0


def test_pre_filter_top1_returns_none_when_all_cases_have_zero_pre_filter() -> None:
    # Defensive: metric is undefined if the filter killed everything.
    results = [_case(case_id="killed", top_slot_tags=[], expected_slots=["a"], pre_filter_count=0)]
    assert pre_filter_top1(results) is None


def test_metric_report_contains_new_fields() -> None:
    results = [
        _case(case_id="c1", top_slot_tags=["a", "b"], expected_slots=["a"], pre_filter_count=3),
        _case(case_id="c2", top_slot_tags=["x"], expected_slots=["a"], pre_filter_count=2),
    ]
    report: MetricReport = compute_metrics(results)
    # mrr = mean(1, 0) = 50; top1 = 1/2 = 50; pre_filter_top1 = 1/2 = 50.
    assert report.mrr == 50.0
    assert report.top1_accuracy == 50.0
    assert report.pre_filter_top1 == 50.0


def test_compute_metrics_empty_results_have_none_for_pre_filter_top1() -> None:
    report = compute_metrics([])
    assert report.mrr == 0.0
    assert report.top1_accuracy == 0.0
    assert report.pre_filter_top1 is None
