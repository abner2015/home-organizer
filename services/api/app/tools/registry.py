"""ToolRegistry — dependency bundle of the 13 agent-callable tools.

The agent pipeline and the API endpoints both depend on a ``ToolRegistry``
rather than directly importing the 4 tool modules, so tests can substitute
scripted callables (e.g. canned ``get_storage_slots`` results) without
monkey-patching imports.

Every callable has the signature
``async (db, *, home_id[, user_id], **kwargs) -> dict | list[dict]``
and raises :class:`AppError` subclasses on genuine failures (cross-home,
missing id, etc.). Empty result = empty list, not error.
"""
from __future__ import annotations

from collections.abc import Callable as _Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

# JSON-safe result types
ToolResult = dict[str, object] | list[dict[str, object]] | list[object]


# A loose type alias — Callable[..., Awaitable[ToolResult]]
# kept as a string in the dataclass annotations to avoid importing
# the Awaitable/ParamSpec machinery for readability.
ToolFn = _Callable[..., object]


@dataclass(slots=True, frozen=True)
class ToolRegistry:
    """All 13 tools the agent pipeline may call."""

    # ---- Home / structure (5) ----
    get_home: ToolFn
    get_rooms: ToolFn
    get_storage_units: ToolFn
    get_storage_sections: ToolFn
    get_storage_slots: ToolFn

    # ---- Items (3) ----
    get_items: ToolFn
    search_items: ToolFn
    get_item_placements: ToolFn

    # ---- Context (2) ----
    get_user_preferences: ToolFn
    get_home_rules: ToolFn

    # ---- Writes (3) ----
    create_recommendation: ToolFn
    verify_recommendation: ToolFn
    save_placement: ToolFn


def get_default_registry(db: AsyncSession) -> ToolRegistry:
    """Build a ``ToolRegistry`` whose callables all share the given session.

    The returned registry's tools are thin closures over the modules in
    ``app/tools/{home,item,context,write}_tools.py`` and are stateless
    w.r.t. the session — i.e. the same ``db`` is passed every call.
    """
    from app.tools.context_tools import (
        get_home_rules as _get_home_rules,
    )
    from app.tools.context_tools import (
        get_user_preferences as _get_user_preferences,
    )
    from app.tools.home_tools import (
        get_home as _get_home,
    )
    from app.tools.home_tools import (
        get_rooms as _get_rooms,
    )
    from app.tools.home_tools import (
        get_storage_sections as _get_storage_sections,
    )
    from app.tools.home_tools import (
        get_storage_slots as _get_storage_slots,
    )
    from app.tools.home_tools import (
        get_storage_units as _get_storage_units,
    )
    from app.tools.item_tools import (
        get_item_placements as _get_item_placements,
    )
    from app.tools.item_tools import (
        get_items as _get_items,
    )
    from app.tools.item_tools import (
        search_items as _search_items,
    )
    from app.tools.write_tools import (
        create_recommendation as _create_recommendation,
    )
    from app.tools.write_tools import (
        save_placement as _save_placement,
    )
    from app.tools.write_tools import (
        verify_recommendation as _verify_recommendation,
    )

    def _bind(fn: ToolFn) -> ToolFn:
        async def wrapper(*args: object, **kwargs: object) -> ToolResult:
            return await fn(*args, db=db, **kwargs)  # type: ignore[arg-type]

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return ToolRegistry(
        get_home=_bind(_get_home),
        get_rooms=_bind(_get_rooms),
        get_storage_units=_bind(_get_storage_units),
        get_storage_sections=_bind(_get_storage_sections),
        get_storage_slots=_bind(_get_storage_slots),
        get_items=_bind(_get_items),
        search_items=_bind(_search_items),
        get_item_placements=_bind(_get_item_placements),
        get_user_preferences=_bind(_get_user_preferences),
        get_home_rules=_bind(_get_home_rules),
        create_recommendation=_bind(_create_recommendation),
        verify_recommendation=_bind(_verify_recommendation),
        save_placement=_bind(_save_placement),
    )


__all__ = ["ToolFn", "ToolRegistry", "ToolResult", "get_default_registry"]
