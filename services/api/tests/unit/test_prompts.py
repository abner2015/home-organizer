"""Tests for the prompt template loader."""
from __future__ import annotations

import pytest

from app.agent.prompts import clear_cache, render


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_cache()
    yield
    clear_cache()


def test_vision_template_renders_hint() -> None:
    out = render("vision", version=1, hint="这是一只马克杯")
    assert "这是一只马克杯" in out
    assert "VisionOutput" in out  # schema reference present


def test_vision_template_handles_missing_hint() -> None:
    out = render("vision", version=1)
    # Empty hint still renders without raising.
    assert "SYSTEM" in out
    assert "USER" in out


def test_vision_v2_grounds_the_category_and_asks_for_sensitivity_fields() -> None:
    """v2 replaced v1's room-type category examples with the real vocabulary."""
    out = render(
        "vision",
        version=2,
        hint="一只马克杯",
        home_context="可选 category：decor, utensil",
    )
    assert "一只马克杯" in out
    assert "可选 category：decor, utensil" in out
    # The two new boolean fields are requested and default-safe.
    assert "is_sensitive" in out
    assert "needs_lock" in out
    assert "不确定时一律填 false" in out
    # v1's room-type misdirection must not have leaked into v2.
    assert "厨房 / 卧室" not in out


def test_vision_v2_escapes_its_literal_braces() -> None:
    """`render` is `str.format_map`, so schema braces must be doubled."""
    out = render("vision", version=2, hint="", home_context="")
    assert '"name"' in out  # braces resolved, not swallowed
    assert "{{" not in out


def test_infer_template_renders_name_and_context() -> None:
    out = render(
        "infer",
        version=1,
        item_name="雨伞",
        item_description="长柄的",
        home_context="可选 category：decor, misc",
    )
    assert "雨伞" in out
    assert "长柄的" in out
    assert "可选 category：decor, misc" in out
    assert "ItemInferenceOutput" in out


def test_infer_template_survives_a_missing_description() -> None:
    out = render(
        "infer", version=1, item_name="雨伞", item_description="", home_context=""
    )
    assert "雨伞" in out
    assert "SYSTEM" in out and "USER" in out
    # Every placeholder was substituted — none survived as literal text.
    assert "{item_name}" not in out
    assert "{item_description}" not in out
    assert "{home_context}" not in out


def test_unknown_template_raises() -> None:
    with pytest.raises(FileNotFoundError):
        render("does_not_exist", version=1)


def test_clear_cache_forces_reload() -> None:
    render("vision", version=1)
    clear_cache()
    out = render("vision", version=1)
    assert out  # still works


def test_search_v3_carries_the_history_block() -> None:
    """v3 is the multi-turn prompt: history must land in the body, and the
    first turn must say so explicitly rather than leaving a bare heading."""
    out = render(
        "search",
        version=3,
        user_query="那它放卧室合适吗？",
        home_context="可选 category：decor",
        history_block="对话历史（最近在前）：\n用户：数据线在哪",
    )
    assert "那它放卧室合适吗？" in out
    assert "可选 category：decor" in out
    assert "用户：数据线在哪" in out
    # The multi-turn rules are what tell the model to resolve 它/那里.
    assert "它" in out and "放哪好" in out
    # Formatting braces were escaped, so the schema rendered as literal JSON.
    assert '"intent"' in out
    assert "{{" not in out


def test_search_v4_adds_the_structure_intent() -> None:
    """v4 is v3 + describe_storage; 「我家有几个柜子？」 must have somewhere to go."""
    out = render(
        "search",
        version=4,
        user_query="我家有几个柜子？",
        home_context="收纳结构：房间 4 个",
        history_block="对话历史：（这是本轮对话的第一句话，没有更早的上下文。）",
    )
    # The new enum value and its definition.
    assert "describe_storage" in out
    assert "我家有几个柜子？" in out
    # The v3 multi-turn block survives into v4.
    assert "放哪好" in out
    assert "对话历史" in out
    # v4's own rule: structure words must never be shoved into `query`.
    assert "绝对不要" in out and "query" in out
    assert "收纳结构：房间 4 个" in out
    # Braces still escaped — the schema must render as literal JSON.
    assert "{{" not in out


def test_search_v4_names_every_intent_in_the_enum_line() -> None:
    from app.agents.search.intent import SearchIntentKind

    out = render(
        "search",
        version=4,
        user_query="q",
        home_context="",
        history_block="",
    )
    for kind in SearchIntentKind:
        assert kind.value in out, f"{kind.value} missing from the v4 enum line"


def test_answer_template_carries_draft_and_forbids_invention() -> None:
    out = render(
        "answer",
        version=1,
        user_query="数据线在哪？",
        draft="数据线在 书房/书桌抽屉/第2格。",
        history_block="对话历史：（这是本轮对话的第一句话，没有更早的上下文。）",
    )
    assert "数据线在 书房/书桌抽屉/第2格。" in out
    assert "数据线在哪？" in out
    assert "严禁编造" in out
    assert "{draft}" not in out
    assert "{user_query}" not in out
    assert "{history_block}" not in out
