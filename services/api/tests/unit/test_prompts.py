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


def test_unknown_template_raises() -> None:
    with pytest.raises(FileNotFoundError):
        render("does_not_exist", version=1)


def test_clear_cache_forces_reload() -> None:
    render("vision", version=1)
    clear_cache()
    out = render("vision", version=1)
    assert out  # still works
