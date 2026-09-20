"""CLI entry point — ``python -m app.evaluation``.

Loads the dataset, runs the recommendation agent against every case
(Mock AI by default; opt-in Real AI via ``EVAL_USE_REAL_AI=1``), then
writes JSON / CSV / Markdown reports to ``evaluation/reports/``.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from app.core.logging import configure_logging, get_logger
from app.evaluation.dataset import load_dataset
from app.evaluation.metrics import compute_metrics
from app.evaluation.reporters import write_csv, write_json, write_markdown
from app.evaluation.runner import EvalRunner, EvalRunnerConfig

logger = get_logger(__name__)

_PACKAGE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = _PACKAGE_DIR / "evaluation" / "reports"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="Run the AI evaluation suite against the recommendation agent.",
    )
    p.add_argument(
        "--use-real-ai",
        action="store_true",
        help="Use the real AI provider (requires API key + EVAL_USE_REAL_AI=1).",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=DEFAULT_REPORT_DIR,
        help="Directory to write JSON / CSV / Markdown reports into.",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max retries the agent may use (default 2).",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-case progress output.",
    )
    return p.parse_args(argv)


async def _main_async(args: argparse.Namespace) -> int:
    if args.use_real_ai and os.getenv("EVAL_USE_REAL_AI") != "1":
        # Real AI is opt-in. We still allow the flag to be set for symmetry,
        # but require the env var to also be set, so a stray --use-real-ai
        # never accidentally burns real API credits.
        print(
            "Refusing to use real AI: set EVAL_USE_REAL_AI=1 in the env to enable.",
            file=sys.stderr,
        )
        return 2

    cases = load_dataset()
    print(f"Loaded {len(cases)} cases from evaluation/dataset/")

    def on_progress(idx: int, total: int, case) -> None:
        if args.quiet:
            return
        print(f"  [{idx:>3}/{total}] {case.id} ({case.category})")

    config = EvalRunnerConfig(
        use_real_ai=args.use_real_ai,
        max_retries=args.max_retries,
        on_progress=on_progress,
    )
    runner = EvalRunner(config)

    started = time.perf_counter()
    results = await runner.run_all(cases)
    elapsed = time.perf_counter() - started
    metrics = compute_metrics(results)

    report_dir: Path = args.report_dir
    json_path = write_json(report_dir / "report.json", results, metrics)
    csv_path = write_csv(report_dir / "report.csv", results)
    md_path = write_markdown(
        report_dir / "report.md",
        results,
        metrics,
        used_real_ai=args.use_real_ai,
    )

    print()
    print("=== Headline Metrics ===")
    print(f"  Total cases:        {metrics.total}")
    print(f"  Passed / Failed:    {metrics.passed_cases} / {metrics.failed_cases}")
    print(f"  Valid slot rate:    {metrics.valid_slot_rate:.2f}%")
    print(f"  Hard violation:     {metrics.hard_constraint_violation_rate:.2f}%")
    print(f"  Recommendation acc: {metrics.recommendation_accuracy:.2f}%")
    print(f"  Top-3 recall:       {metrics.top3_recommendation_recall:.2f}%")
    print(f"  Retry success:      {metrics.retry_success_rate:.2f}%")
    print(f"  Hallucinated slots: {metrics.hallucinated_slot_rate:.2f}%")
    print(f"  Item recognition:   {metrics.item_recognition_accuracy if metrics.item_recognition_accuracy is not None else 'N/A'}")
    print(f"  Verifier catch:     {metrics.verifier_catch_rate if metrics.verifier_catch_rate is not None else 'N/A'}")
    print()
    print(f"Reports written to {report_dir}:")
    print(f"  - {json_path}")
    print(f"  - {csv_path}")
    print(f"  - {md_path}")
    print(f"Total runtime: {elapsed:.2f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    configure_logging("WARNING")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())