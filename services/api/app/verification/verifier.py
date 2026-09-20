"""Verifier orchestrator — runs the 9 checks and aggregates the result."""
from __future__ import annotations

from app.verification.checks import ALL_CHECKS
from app.verification.context import CheckResult, VerificationContext, VerificationResult


def run_verifier(
    ctx: VerificationContext, candidate: dict[str, object]
) -> VerificationResult:
    """Run every check in order; stop at the first failure? No — run all so the
    orchestrator can log all violations; the LLM retry only needs the first.
    """
    passed: list[CheckResult] = []
    failed: list[CheckResult] = []
    for fn in ALL_CHECKS:
        result = fn(ctx, candidate)  # type: ignore[arg-type]
        (passed if result.passed else failed).append(result)
    return VerificationResult(
        ok=not failed,
        failed=tuple(failed),
        passed=tuple(passed),
    )


__all__ = ["run_verifier"]
