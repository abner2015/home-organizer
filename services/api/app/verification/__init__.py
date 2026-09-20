"""Verifier — pure checks that gate a recommendation decision.

The Verifier never touches the DB. The orchestrator precomputes a
:class:`VerificationContext` from the agent's read-only tool results, then
calls :func:`run_verifier` to produce a :class:`VerificationResult`. Each
of the 9 checks returns a :class:`CheckResult`; the verifier aggregates
them into a single pass/fail verdict plus a human-readable message.

The pipeline consults ``result.failed[0]`` to decide whether to retry
the LLM Decision step.
"""
from app.verification.context import CheckResult, VerificationContext, VerificationResult
from app.verification.verifier import run_verifier

__all__ = [
    "CheckResult",
    "VerificationContext",
    "VerificationResult",
    "run_verifier",
]
