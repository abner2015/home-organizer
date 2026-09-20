"""Prompt template loader.

Templates live in ``app/agent/prompts/<name>.v<n>.md``. They are loaded
into memory on first access (memoized via :mod:`functools.lru_cache`) so
hot paths don't touch the filesystem.

A template body uses ``{{var}}`` placeholders. Rendering is plain
``str.format_map`` with a :class:`SafeDict` that leaves unknown keys
empty rather than raising — that way adding a new variable to a
template can't crash older callers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


class _SafeDict(dict[str, object]):
    """``str.format_map`` mapping that yields ``""`` for missing keys."""

    def __missing__(self, key: str) -> str:  # pragma: no cover - defensive
        return ""


@lru_cache(maxsize=32)
def _read(name_version: str) -> str:
    """Read and cache a template file by ``"<name>.v<n>"`` id."""
    path = _PROMPTS_DIR / f"{name_version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path.name}")
    return path.read_text(encoding="utf-8")


def render(name: str, version: int = 1, **variables: object) -> str:
    """Load ``<name>.v<version>.md`` and render with ``variables``.

    The rendered string includes the full template body (SYSTEM + USER).
    Providers that need them split (e.g. Anthropic's ``system`` param)
    can split on the ``---`` separator.
    """
    body = _read(f"{name}.v{version}")
    return body.format_map(_SafeDict(variables))


def clear_cache() -> None:
    """Drop the in-memory cache. Tests use this between cases."""
    _read.cache_clear()


__all__ = ["clear_cache", "render"]
