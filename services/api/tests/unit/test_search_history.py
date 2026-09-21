"""Tests for the assistant's conversation-history renderer.

These are pure-function tests: no DB, no LLM. They pin the transcript shape
the intent prompt relies on, plus the two truncation rules that keep the
prompt bounded (turn count and per-message length).
"""
from __future__ import annotations

from app.agents.search.history import (
    MAX_HISTORY_TURNS,
    MAX_MESSAGE_CHARS,
    format_history,
    render_history_block,
)


def test_empty_history_renders_empty_string() -> None:
    assert format_history([]) == ""


def test_transcript_labels_roles_in_chinese() -> None:
    out = format_history(
        [("user", "我的数据线在哪里？"), ("assistant", "数据线在书桌抽屉。")]
    )
    assert out == "用户：我的数据线在哪里？\n助手：数据线在书桌抽屉。"


def test_blank_and_unknown_roles_are_skipped() -> None:
    """``role`` is constrained to user/assistant/tool in the DB; a blank
    content row must not produce a dangling "助手：" line."""
    out = format_history(
        [("user", "  "), ("tool", "raw tool output"), ("assistant", "好的。")]
    )
    assert out == "助手：好的。"


def test_only_the_last_turns_are_kept() -> None:
    messages = [
        (("user" if i % 2 == 0 else "assistant"), f"第{i}句")
        for i in range(MAX_HISTORY_TURNS + 4)
    ]
    out = format_history(messages)
    assert out.count("\n") == MAX_HISTORY_TURNS - 1
    # The oldest messages fell off; the newest one did not.
    assert "第0句" not in out
    assert f"第{MAX_HISTORY_TURNS + 3}句" in out


def test_long_messages_are_clipped() -> None:
    long_answer = "很长的回答" * 100
    out = format_history([("assistant", long_answer)])
    assert out.endswith("…")
    assert len(out) < len(long_answer)


def test_whitespace_is_collapsed_so_a_multiline_answer_stays_one_line() -> None:
    """Multi-line assistant answers would otherwise break the 1-line-per-turn
    shape the prompt instructions describe."""
    out = format_history([("assistant", "第一行\n\n第二行")])
    assert out == "助手：第一行 第二行"


def test_block_wraps_the_transcript() -> None:
    out = render_history_block("用户：你好")
    assert out.startswith("对话历史（最近在前）：")
    assert "用户：你好" in out


def test_block_states_explicitly_when_there_is_no_context() -> None:
    """A bare heading with nothing under it invites the model to invent a
    previous exchange, so the first turn says so outright."""
    out = render_history_block("")
    assert "第一句话" in out
    assert "\n" not in out


def test_clip_boundary_is_exact() -> None:
    out = format_history([("user", "x" * MAX_MESSAGE_CHARS)])
    assert out == "用户：" + "x" * MAX_MESSAGE_CHARS
