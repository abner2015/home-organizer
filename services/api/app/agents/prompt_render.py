"""Prompt builder for the Rank step (LLM Decision).

Composes the recommend.v2.md template with item / candidates / rules /
preferences / history / last_failure. Pure function — no I/O, no DB.

v2 (append-only: v1 kept) only tightens the ``reason`` instructions — it must
be written in Chinese and must not quote slot ``code``s / English field names,
because the reason is rendered straight into the UI (see
``recommend.v2.md`` §「reason 的写法」).
"""
from __future__ import annotations

import json
from typing import Any

from app.agent.prompts import render


def _safe_json(value: Any) -> str:
    """JSON-serialise with a safe fallback. ``ensure_ascii=False`` so Chinese
    characters stay readable when the LLM prints them back."""
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def build_rank_prompt(
    *,
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    preferences: list[dict[str, Any]],
    history: list[dict[str, Any]],
    last_failure: str | None = None,
) -> str:
    """Render ``prompts/recommend.v2.md`` with the candidate decision inputs."""
    return render(
        "recommend",
        version=2,
        item_json=_safe_json(item),
        candidates_json=_safe_json(candidates),
        rules_json=_safe_json(rules),
        preferences_json=_safe_json(preferences),
        history_json=_safe_json(history),
        last_failure=last_failure or "(无)",
    )


__all__ = ["build_rank_prompt"]
