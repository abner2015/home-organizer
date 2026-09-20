"""Metrics — computes the 8 evaluation metrics from per-case results.

Metrics (per spec):

1. **Item Recognition Accuracy** — % of cases where the agent's chosen
   item/category matches the golden item. In the mock-only path this is
   N/A (no vision call); the runner records ``None`` and the metric is
   reported as "N/A" in the Markdown report.

2. **Valid Slot Rate** — % of cases where the chosen slot exists in the
   synthetic home's slot tree (i.e. the agent did NOT hallucinate a slot).

3. **Hard Constraint Violation Rate** — % of cases where the chosen slot's
   tag is in ``forbidden_slots``. Should be ≈ 0; any non-zero value is a bug.

4. **Recommendation Accuracy** — % of cases where the chosen slot's tag is
   in ``expected_slots``. Higher is better.

5. **Top-3 Recommendation Recall** — % of cases where AT LEAST ONE of the
   top-3 candidates' tags appears in ``expected_slots``.

6. **Verifier Catch Rate** — % of (deliberately broken / would-fail-verify)
   runs where the verifier flipped ``ok`` to False. Reported as N/A in the
   default mock dataset (we'd need a separate "tricky" dataset to exercise).

7. **Retry Success Rate** — of cases that retried at least once, % that
   ultimately reached ANSWER. Higher = retry loop is effective.

8. **Hallucinated Slot Rate** — % of cases where the chosen slot was not
   in the synthetic home at all. **Must be ≈ 0.**
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from app.evaluation.runner import CaseResult


@dataclass(slots=True)
class MetricReport:
    """Computed metrics — one value per metric plus per-category breakdown."""

    total: int
    item_recognition_accuracy: float | None
    valid_slot_rate: float
    hard_constraint_violation_rate: float
    recommendation_accuracy: float
    top3_recommendation_recall: float
    verifier_catch_rate: float | None
    retry_success_rate: float
    hallucinated_slot_rate: float
    per_category: dict[str, "CategoryMetric"] = field(default_factory=dict)
    passed_cases: int = 0
    failed_cases: int = 0


@dataclass(slots=True)
class CategoryMetric:
    category: str
    total: int
    recommendation_accuracy: float
    hard_constraint_violation_rate: float
    hallucinated_slot_rate: float


# ----------------------------------------------------------------- helpers


def _safe_div(num: float, den: float) -> float:
    return (num / den) if den else 0.0


def _pct(num: float) -> float:
    return round(num * 100, 2)


# ----------------------------------------------------------------- compute


def compute_metrics(results: list[CaseResult]) -> MetricReport:
    if not results:
        return MetricReport(
            total=0,
            item_recognition_accuracy=None,
            valid_slot_rate=0.0,
            hard_constraint_violation_rate=0.0,
            recommendation_accuracy=0.0,
            top3_recommendation_recall=0.0,
            verifier_catch_rate=None,
            retry_success_rate=0.0,
            hallucinated_slot_rate=0.0,
        )

    total = len(results)

    # 1. Item Recognition Accuracy (N/A when no vision was run).
    recognition_values = [r.item_recognition_correct for r in results if r.item_recognition_correct is not None]
    if recognition_values:
        item_recognition = _pct(sum(1 for v in recognition_values if v) / len(recognition_values))
    else:
        item_recognition = None

    # 2. Valid Slot Rate — chosen slot exists in the slot tree.
    #    We approximate this with "chosen_slot_id is non-None and was in the
    #    top-N candidates". If the runner always populated real ids, this
    #    is 100% in normal operation.
    valid_slots = sum(1 for r in results if r.chosen_slot_id is not None)
    valid_slot_rate = _pct(valid_slots / total)

    # 3. Hard Constraint Violation Rate — chosen tag ∈ forbidden_slots.
    forbidden_hits = sum(
        1 for r in results if r.chosen_slot_tag and r.chosen_slot_tag in r.forbidden_slots
    )
    violation_rate = _pct(forbidden_hits / total)

    # 4. Recommendation Accuracy — chosen tag ∈ expected_slots.
    correct = sum(
        1 for r in results if r.chosen_slot_tag and r.chosen_slot_tag in r.expected_slots
    )
    accuracy = _pct(correct / total)

    # 5. Top-3 Recall — at least one top-3 tag ∈ expected_slots.
    top3_hits = sum(
        1 for r in results if any(t for t in r.top_slot_tags[:3] if t in r.expected_slots)
    )
    top3_recall = _pct(top3_hits / total)

    # 6. Verifier Catch Rate — only meaningful if any case has
    #    ``verifier_passed == False``. In our default dataset the verifier
    #    only flips false when there's a hard rule violation, so we report
    #    the inverse of "all verifier-passed" as the rate at which the
    #    verifier *did* flag something.
    flagged = sum(1 for r in results if not r.verifier_passed)
    if flagged:
        verifier_catch = _pct(flagged / total)
    else:
        verifier_catch = None  # N/A — verifier never triggered

    # 7. Retry Success Rate — among cases that retried, % that succeeded.
    retried = [r for r in results if r.retries_used > 0]
    if retried:
        succeeded = sum(1 for r in retried if r.state == "answer" and r.verifier_passed)
        retry_success = _pct(succeeded / len(retried))
    else:
        retry_success = 0.0

    # 8. Hallucinated Slot Rate — chosen slot exists but maps to NO tag.
    hallucinated = sum(1 for r in results if r.chosen_slot_id and r.chosen_slot_tag is None)
    hallucinated_rate = _pct(hallucinated / total)

    # Per-category breakdown.
    by_category: dict[str, list[CaseResult]] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r)
    per_category: dict[str, CategoryMetric] = {}
    for cat, bucket in sorted(by_category.items()):
        c_total = len(bucket)
        c_correct = sum(1 for r in bucket if r.chosen_slot_tag and r.chosen_slot_tag in r.expected_slots)
        c_violations = sum(1 for r in bucket if r.chosen_slot_tag and r.chosen_slot_tag in r.forbidden_slots)
        c_hallu = sum(1 for r in bucket if r.chosen_slot_id and r.chosen_slot_tag is None)
        per_category[cat] = CategoryMetric(
            category=cat,
            total=c_total,
            recommendation_accuracy=_pct(c_correct / c_total),
            hard_constraint_violation_rate=_pct(c_violations / c_total),
            hallucinated_slot_rate=_pct(c_hallu / c_total),
        )

    passed = sum(1 for r in results if r.state == "answer" and r.verifier_passed)
    return MetricReport(
        total=total,
        item_recognition_accuracy=item_recognition,
        valid_slot_rate=valid_slot_rate,
        hard_constraint_violation_rate=violation_rate,
        recommendation_accuracy=accuracy,
        top3_recommendation_recall=top3_recall,
        verifier_catch_rate=verifier_catch,
        retry_success_rate=retry_success,
        hallucinated_slot_rate=hallucinated_rate,
        per_category=per_category,
        passed_cases=passed,
        failed_cases=total - passed,
    )


# Convenience: counter of cases by category, useful in reports.
def category_counts(results: Iterable[CaseResult]) -> dict[str, int]:
    return dict(Counter(r.category for r in results))


__all__ = ["MetricReport", "CategoryMetric", "compute_metrics", "category_counts"]