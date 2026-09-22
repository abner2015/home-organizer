"""Tests for the ``structure.v1.md`` template.

Templates are rendered with ``str.format_map``, not Jinja2, so a literal brace
in the JSON example has to be doubled. Getting that wrong does not fail loudly
— it silently deletes a chunk of the example the model is supposed to copy — so
the escaping is pinned here rather than trusted.
"""
from __future__ import annotations

from app.agent.prompts import render


def _rendered() -> str:
    return render(
        "structure",
        version=1,
        home_context="【家中真实数据】CTX-SENTINEL",
        input_note="NOTE-SENTINEL",
    )


def test_both_variables_are_substituted() -> None:
    text = _rendered()
    assert "CTX-SENTINEL" in text
    assert "NOTE-SENTINEL" in text
    # `_SafeDict` renders a missing key as "", which would leave the template
    # readable but ungrounded — so assert the placeholders themselves are gone.
    assert "{home_context}" not in text
    assert "{input_note}" not in text


def test_literal_braces_survive_rendering() -> None:
    """The JSON example must reach the model with single braces."""
    text = _rendered()
    assert '{{' not in text
    assert '}}' not in text
    # A key from the example, i.e. a brace that was escaped and unwrapped.
    assert '"rooms"' in text
    assert '"capacity_hint"' in text


def test_no_bare_placeholder_braces_are_left_behind() -> None:
    """Every remaining brace must belong to the JSON example, not a variable."""
    text = _rendered()
    assert text.count("{") == text.count("}")
