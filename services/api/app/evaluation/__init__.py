"""AI Evaluation framework (Phase 8+).

Goal: test *AI decision quality*, not code correctness. We seed a home with a
known slot hierarchy, run the recommendation agent on each item, then compare
the agent's output against the expected / forbidden slots declared in the
golden dataset.

Modules:

- ``dataset`` — loads golden cases from ``evaluation/dataset/*.json``.
- ``runner``   — instantiates Home + Storage tree per case and runs the agent.
- ``metrics``  — computes the 8 named metrics from per-case results.
- ``reporters``— JSON / CSV / Markdown output.

Run via ``python -m app.evaluation`` (defaults to Mock AI; opt into Real AI
with ``EVAL_USE_REAL_AI=1``).
"""
from app.evaluation.dataset import EvalCase, load_dataset
from app.evaluation.runner import CaseResult, EvalRunner, EvalRunnerConfig
from app.evaluation.metrics import MetricReport, compute_metrics

__all__ = [
    "EvalCase",
    "load_dataset",
    "CaseResult",
    "EvalRunner",
    "EvalRunnerConfig",
    "MetricReport",
    "compute_metrics",
]