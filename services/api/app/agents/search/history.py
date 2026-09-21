"""Conversation history rendering for the search agent.

The assistant used to be stateless: every query was parsed in isolation, so a
follow-up like 「那它放哪好？」 had no referent and the model answered
something unrelated. This module turns the persisted ``Message`` rows of a
conversation into a compact Chinese transcript the intent prompt (and the
answer-composition prompt) can read.

Pure functions only — no DB, no LLM — so the shape and the truncation rules
are directly testable.
"""
from __future__ import annotations

from collections.abc import Sequence

# How many prior messages (user + assistant counted together) we replay.
# Small on purpose: the intent prompt already carries the whole home
# inventory, and an unbounded transcript would drown it.
MAX_HISTORY_TURNS = 8

# Per-message character cap. A long assistant answer adds nothing to the
# *meaning* of the next question, so clipping keeps the prompt bounded
# without a token estimator.
MAX_MESSAGE_CHARS = 200

_ROLE_LABEL = {"user": "用户", "assistant": "助手"}


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_MESSAGE_CHARS:
        return text
    return text[:MAX_MESSAGE_CHARS] + "…"


def format_history(
    messages: Sequence[tuple[str, str]],
    *,
    max_turns: int = MAX_HISTORY_TURNS,
) -> str:
    """Render ``[(role, content), ...]`` as a Chinese transcript block.

    ``role`` is expected to be ``"user"`` or ``"assistant"`` (the values the
    ``messages.role`` CHECK constraint allows); anything else is skipped.
    Only the **last** ``max_turns`` messages are kept — most recent context
    is what a follow-up refers to.

    Returns ``""`` for an empty transcript, which callers treat as "no prior
    context" (first turn).
    """
    recent = [
        (role, content)
        for role, content in messages
        if role in _ROLE_LABEL and content.strip()
    ][-max_turns:]
    if not recent:
        return ""
    return "\n".join(f"{_ROLE_LABEL[role]}：{_clip(content)}" for role, content in recent)


def render_history_block(history: str) -> str:
    """Wrap a transcript in the prompt block the templates embed.

    Kept separate from :func:`format_history` so the block wording lives in
    one place and the first turn (empty history) renders an explicit
    "no prior context" line rather than a dangling heading — a bare
    ``对话历史：`` heading with nothing under it invites the model to
    hallucinate a previous exchange.
    """
    if not history.strip():
        return "对话历史：（这是本轮对话的第一句话，没有更早的上下文。）"
    return f"对话历史（最近在前）：\n{history}"


__all__ = [
    "MAX_HISTORY_TURNS",
    "MAX_MESSAGE_CHARS",
    "format_history",
    "render_history_block",
]
