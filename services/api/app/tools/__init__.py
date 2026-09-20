"""Tools — the function-call surface for the recommendation agent.

Every tool is an async callable that takes an ``AsyncSession`` and a keyword-only
``home_id`` (and optionally ``user_id``) and returns JSON-safe data. Tools that
operate on a specific resource also take the resource id as a keyword arg.

The :class:`ToolRegistry` dataclass bundles the 13 tools the agent pipeline
needs so they can be injected and (in tests) swapped for scripted mocks.
"""
from app.tools.registry import ToolRegistry, get_default_registry

__all__ = ["ToolRegistry", "get_default_registry"]
