"""Output reporters — JSON, CSV, Markdown.

The CLI (``app.evaluation.__main__``) writes all three formats to
``evaluation/reports/``. The Markdown report is the human-readable summary;
JSON is the raw data for downstream tooling; CSV is for spreadsheets.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.evaluation.metrics import MetricReport
from app.evaluation.runner import CaseResult


# ----------------------------------------------------------------- JSON


def render_json(
    results: list[CaseResult],
    metrics: MetricReport,
    *,
    indent: int = 2,
) -> str:
    payload: dict[str, Any] = {
        "summary": asdict(metrics),
        "cases": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "expected_slots": r.expected_slots,
                "forbidden_slots": r.forbidden_slots,
                "chosen_slot_id": r.chosen_slot_id,
                "chosen_slot_tag": r.chosen_slot_tag,
                "top_slot_ids": r.top_slot_ids,
                "top_slot_tags": r.top_slot_tags,
                "state": r.state,
                "retries_used": r.retries_used,
                "verifier_passed": r.verifier_passed,
                "item_recognition_correct": r.item_recognition_correct,
                "error": r.error,
                "duration_ms": r.duration_ms,
                "reason": r.reason,
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def write_json(
    path: Path | str,
    results: list[CaseResult],
    metrics: MetricReport,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_json(results, metrics), encoding="utf-8")
    return p


# ----------------------------------------------------------------- CSV


CSV_COLUMNS: list[str] = [
    "case_id",
    "category",
    "expected_slots",
    "forbidden_slots",
    "chosen_slot_id",
    "chosen_slot_tag",
    "top_slot_tags",
    "state",
    "retries_used",
    "verifier_passed",
    "item_recognition_correct",
    "error",
    "duration_ms",
]


def render_csv(results: list[CaseResult]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for r in results:
        writer.writerow(
            {
                "case_id": r.case_id,
                "category": r.category,
                "expected_slots": "|".join(r.expected_slots),
                "forbidden_slots": "|".join(r.forbidden_slots),
                "chosen_slot_id": r.chosen_slot_id or "",
                "chosen_slot_tag": r.chosen_slot_tag or "",
                "top_slot_tags": "|".join(t for t in r.top_slot_tags if t),
                "state": r.state,
                "retries_used": r.retries_used,
                "verifier_passed": r.verifier_passed,
                "item_recognition_correct": (
                    "" if r.item_recognition_correct is None
                    else str(r.item_recognition_correct).lower()
                ),
                "error": r.error or "",
                "duration_ms": r.duration_ms,
            }
        )
    return buf.getvalue()


def write_csv(path: Path | str, results: list[CaseResult]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_csv(results), encoding="utf-8")
    return p


# ----------------------------------------------------------------- Markdown


def _fmt(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def render_markdown(
    results: list[CaseResult],
    metrics: MetricReport,
    *,
    used_real_ai: bool = False,
) -> str:
    ai_mode = "Real AI" if used_real_ai else "Mock AI"
    lines: list[str] = []
    lines.append("# AI Evaluation Report")
    lines.append("")
    lines.append(f"- **AI mode:** {ai_mode}")
    lines.append(f"- **Total cases:** {metrics.total}")
    lines.append(f"- **Passed:** {metrics.passed_cases}")
    lines.append(f"- **Failed:** {metrics.failed_cases}")
    lines.append("")
    lines.append("## Headline Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| 1. Item Recognition Accuracy | {_fmt(metrics.item_recognition_accuracy)} |")
    lines.append(f"| 2. Valid Slot Rate | {_fmt(metrics.valid_slot_rate)} |")
    lines.append(f"| 3. Hard Constraint Violation Rate | {_fmt(metrics.hard_constraint_violation_rate)} |")
    lines.append(f"| 4. Recommendation Accuracy | {_fmt(metrics.recommendation_accuracy)} |")
    lines.append(f"| 5. Top-3 Recommendation Recall | {_fmt(metrics.top3_recommendation_recall)} |")
    lines.append(f"| 6. Verifier Catch Rate | {_fmt(metrics.verifier_catch_rate)} |")
    lines.append(f"| 7. Retry Success Rate | {_fmt(metrics.retry_success_rate)} |")
    lines.append(f"| 8. Hallucinated Slot Rate | {_fmt(metrics.hallucinated_slot_rate)} |")
    lines.append("")
    lines.append("> **Hallucinated Slot Rate must be ≈ 0.** Any non-zero value indicates the agent is choosing slots that don't exist in the home — a critical safety bug.")
    lines.append("")
    if metrics.per_category:
        lines.append("## Per-Category Breakdown")
        lines.append("")
        lines.append("| Category | Cases | Recommendation Accuracy | Hard Violations | Hallucinated |")
        lines.append("| --- | --- | --- | --- | --- |")
        for cat, m in metrics.per_category.items():
            lines.append(
                f"| {cat} | {m.total} | {_fmt(m.recommendation_accuracy)} | "
                f"{_fmt(m.hard_constraint_violation_rate)} | {_fmt(m.hallucinated_slot_rate)} |"
            )
        lines.append("")

    # Failure detail — only show cases that didn't pass.
    failures = [r for r in results if not (r.state == "answer" and r.verifier_passed)]
    if failures:
        lines.append("## Failures")
        lines.append("")
        lines.append("| Case | Category | Expected | Chosen | State | Error |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for r in failures:
            expected = ", ".join(r.expected_slots) or "—"
            chosen = r.chosen_slot_tag or "—"
            lines.append(
                f"| `{r.case_id}` | {r.category} | {expected} | {chosen} | "
                f"{r.state} | {r.error or ''} |"
            )
        lines.append("")

    return "\n".join(lines)


def write_markdown(
    path: Path | str,
    results: list[CaseResult],
    metrics: MetricReport,
    *,
    used_real_ai: bool = False,
) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        render_markdown(results, metrics, used_real_ai=used_real_ai),
        encoding="utf-8",
    )
    return p


__all__ = [
    "CSV_COLUMNS",
    "render_json",
    "render_csv",
    "render_markdown",
    "write_json",
    "write_csv",
    "write_markdown",
]