"""Pure answer-formatters for the search agent (Phase 6).

Each helper takes a list of *candidate dicts* (see :func:`SearchAgent.run`
for shape) and an intent, and returns a Chinese sentence the UI can
display verbatim.

These helpers never touch the database, the LLM, or any side-effecting
state. They are deliberately small and pure so the agent tests can
exercise them in isolation.

Shape contract — ``candidate``::

    {
        "item_id":  uuid.UUID,
        "name":     str,
        "category": str,
        "subcategory": str,
        "is_sensitive": bool,
        "location": {                              # or None if not placed
            "slot_id":     uuid.UUID,
            "code":        str,
            "label":       str,
            "full_path":   str,
            "room_name":   str,
            "unit_name":   str,
            "section_name": str,
        } | None,
    }
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.agent.prompts import render
from app.agents.search.history import render_history_block
from app.agents.search.intent import ExtractedSearchIntent, SearchIntentKind
from app.ai.errors import AIProviderError
from app.ai.provider import AIProvider

# ---------------------------------------------------------------------- helpers


def _location_text(loc: dict[str, Any] | None) -> str:
    """Format a single candidate's location as a Chinese path string.

    Falls back to "未放置" for unplaced items.
    """
    if not loc:
        return "未放置"
    return loc.get("full_path") or "未放置"


def _placed(c: dict[str, Any]) -> bool:
    return bool(c.get("location"))


# ---------------------------------------------------------------------- per-intent formatters


def format_find_item(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format FIND_ITEM answer.

    Returns ``(state, answer_text)`` where ``state`` is one of:

    - ``"answer"`` — exactly one match, the item is placed.
    - ``"exists_but_not_placed"`` — exactly one match but the item has no
      active placement (we can say "家里有X但是还没放好").
    - ``"needs_clarification"`` — multiple matches, list all + ask which.
    - ``"not_found"`` — zero matches.
    """
    if len(candidates) == 0:
        return ("not_found", f"没有找到{intent.query or '该物品'}。")
    if len(candidates) == 1:
        c = candidates[0]
        if _placed(c):
            return (
                "answer",
                f"{c['name']}在 {_location_text(c['location'])}。",
            )
        return (
            "exists_but_not_placed",
            f"家里有{c['name']}，但是还没有放置位置。",
        )
    # Multiple matches → ask the user to pick.
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        if _placed(c):
            lines.append(f"{i}) {c['name']}（{_location_text(c['location'])}）")
        else:
            lines.append(f"{i}) {c['name']}（未放置）")
    body = "；".join(lines)
    return (
        "needs_clarification",
        f"找到多个匹配：{body}。请问您要找的是哪一个？",
    )


def format_find_items(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format FIND_ITEMS answer — always a list, never a clarification."""
    if not candidates:
        # Try to be location-hint-aware in the negative case.
        if intent.location_hint:
            return (
                "not_found",
                f"{intent.location_hint}里没有找到任何「{intent.query or '物品'}」。",
            )
        return ("not_found", f"没有找到「{intent.query or '物品'}」。")
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        if _placed(c):
            lines.append(f"{i}) {c['name']}（{_location_text(c['location'])}）")
        else:
            lines.append(f"{i}) {c['name']}（未放置）")
    body = "；".join(lines)
    head = f"找到了 {len(candidates)} 个匹配"
    if intent.location_hint:
        head = f"在{intent.location_hint}找到了 {len(candidates)} 个匹配"
    return ("answer", f"{head}：{body}。")


def format_find_location(
    intent: ExtractedSearchIntent,
    candidates: list[dict[str, Any]],
    slot_label: str = "",
) -> tuple[str, str]:
    """Format FIND_LOCATION answer — items placed in a named slot/section."""
    if not candidates:
        if slot_label:
            return ("not_found", f"{slot_label}里没有放置任何物品。")
        if intent.location_hint:
            return ("not_found", f"「{intent.location_hint}」里没有放置任何物品。")
        return ("not_found", "该位置没有放置任何物品。")
    lines: list[str] = []
    for i, c in enumerate(candidates, start=1):
        # For find_location we already know each item is placed in the slot;
        # show the item name + its room path for context.
        if c.get("location"):
            lines.append(f"{i}) {c['name']}")
        else:
            lines.append(f"{i}) {c['name']}")
    body = "、".join(lines)
    head = f"{slot_label or intent.location_hint or '该位置'}里放置了：{body}"
    return ("answer", head + "。")


def format_check_existence(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format CHECK_EXISTENCE answer — yes/no + optional location of the first match."""
    if not candidates:
        return ("not_found", f"家里没有{intent.query or '该物品'}。")
    n = len(candidates)
    first = candidates[0]
    head = f"有 {n} 个{intent.query or '物品'}"
    if n == 1:
        head = f"有 1 个{intent.query or '物品'}"
    tail = f"（{_location_text(first['location'])}）" if _placed(first) else "（未放置）"
    if n == 1:
        return ("answer", f"{head}{tail}。")
    # Multiple — mention total + first.
    return ("answer", f"{head}{tail}，还有 {n - 1} 个。")


def format_list_category(
    intent: ExtractedSearchIntent, candidates: list[dict[str, Any]]
) -> tuple[str, str]:
    """Format LIST_CATEGORY answer — group items by room when possible."""
    if not candidates:
        return ("not_found", f"家里没有「{intent.category or '该类别'}」的物品。")
    by_room: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        loc = c.get("location") or {}
        room = loc.get("room_name") or "未放置"
        by_room.setdefault(room, []).append(c)
    parts: list[str] = []
    # Stable iteration order: rooms with placed items first (alphabetical),
    # "未放置" last so the user always sees the answerable info first.
    placed_rooms = sorted(r for r in by_room if r != "未放置")
    if "未放置" in by_room:
        placed_rooms.append("未放置")
    for room in placed_rooms:
        items_here = by_room[room]
        names = "、".join(c["name"] for c in items_here)
        parts.append(f"{room}：{names}")
    body = "；".join(parts)
    head = f"家里共有 {len(candidates)} 件「{intent.category or '该类别'}」物品"
    return ("answer", f"{head}：{body}。")


def format_describe_storage(
    intent: ExtractedSearchIntent, blueprint: str
) -> tuple[str, str]:
    """Format DESCRIBE_STORAGE — the home's storage structure.

    Unlike the item intents there is no candidate list to render: the agent
    hands over an already-rendered blueprint (see
    :func:`app.agents.search.structure.build_home_blueprint`) and this only
    picks the state. An empty blueprint means the home has no rooms and no
    furniture at all, which is a genuine ``not_found`` — not a failure.
    """
    if not blueprint.strip():
        return (
            "not_found",
            "您家里还没有录入任何房间和收纳空间，先去「我的家」里添加，之后我就能帮您统计了。",
        )
    return ("answer", blueprint)


def format_suggest_placement(
    intent: ExtractedSearchIntent,
    *,
    item_name: str,
    item_category: str,
    slot: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Format SUGGEST_PLACEMENT — ``(state, answer_text, reason)``.

    ``slot`` is a *ranked slot dict* (the ranker's own output, which carries
    ``active_count`` and ``allowed_categories``), not the API view. It is
    ``None`` when the deterministic pipeline produced no candidate at all.

    The reason only claims what the data supports: whether the slot's
    ``allowed_categories`` genuinely admits the item, and whether it is
    currently empty. It never shows a confidence — ranked candidates carry
    ``det_score`` but no ``confidence``, so the shared projection would report
    a misleading 0.0.
    """
    if slot is None:
        return (
            "not_found",
            f"我暂时没找到适合放「{item_name}」的位置，请先补充收纳空间。",
            "",
        )

    path = str(slot.get("full_path") or slot.get("label") or slot.get("code") or "")
    where = "/".join(
        str(p)
        for p in (slot.get("room_name"), slot.get("unit_name"), slot.get("section_name"))
        if p
    )
    allowed = [str(a).lower() for a in (slot.get("allowed_categories") or [])]
    category_matched = bool(item_category) and item_category.lower() in allowed
    is_empty = int(slot.get("active_count") or 0) == 0

    reason = f"{where or path}"
    reason += "的收纳类别与它匹配，" if category_matched else "可以收纳它，"
    reason += "目前还有空位。" if is_empty else "目前已有同类物品，仍可放入。"

    return ("answer", f"建议把「{item_name}」放在 {path}。", reason)


# ---------------------------------------------------------------------- entry-point


def format_answer(
    intent: ExtractedSearchIntent,
    candidates: Sequence[dict[str, Any]],
    *,
    slot_label: str = "",
) -> tuple[str, str]:
    """Dispatch to the per-intent formatter and return ``(state, text)``.

    ``slot_label`` is an optional pre-computed label used by
    :func:`format_find_location` when the slot the agent matched on has
    a nicely formatted Chinese path (e.g. "客厅装饰柜L1").
    """
    cands = list(candidates)
    kind = intent.intent
    if kind == SearchIntentKind.FIND_ITEM:
        return format_find_item(intent, cands)
    if kind == SearchIntentKind.FIND_ITEMS:
        return format_find_items(intent, cands)
    if kind == SearchIntentKind.FIND_LOCATION:
        return format_find_location(intent, cands, slot_label=slot_label)
    if kind == SearchIntentKind.CHECK_EXISTENCE:
        return format_check_existence(intent, cands)
    if kind == SearchIntentKind.LIST_CATEGORY:
        return format_list_category(intent, cands)
    if kind == SearchIntentKind.DESCRIBE_STORAGE:
        # The handler owns this flow and short-circuits with the blueprint; a
        # bare candidate list carries no structure, so say so rather than
        # rendering a misleading "not found".
        return ("not_found", "我没能读到您家的收纳空间信息。")
    if kind == SearchIntentKind.SUGGEST_PLACEMENT:
        # The handler owns this flow (it needs the ranked slot, which this
        # signature has no room for). Reached only if the handler ever stops
        # short-circuiting, so keep the message honest rather than crashing.
        return ("not_found", "我没能找到合适的收纳位置。")
    # UNKNOWN / fallback — caller passes the question through.
    question = intent.question or "请告诉我您想找什么物品。"
    return ("needs_clarification", question)


# ---------------------------------------------------------------------- compose


async def compose_answer(
    provider: AIProvider,
    *,
    user_query: str,
    draft: str,
    history: str = "",
    timeout_s: float = 30.0,
) -> str:
    """Let the LLM phrase the final reply, grounded in the retrieved facts.

    ``draft`` is the deterministic sentence :func:`format_answer` produced —
    it already contains every fact we retrieved (item names, Chinese paths,
    counts, or the honest "没找到"). The model's only job is to *say it
    naturally* and to use the conversation history to interpret the follow-up.
    It cannot invent locations: the prompt forbids it and the draft is the
    sole input.

    Best-effort by design. Any :class:`AIProviderError` (timeout, transport,
    quota) returns ``draft`` unchanged, so a chat hiccup degrades the wording
    but never the answer — the endpoint still returns 200 with real data.
    """
    if not draft.strip():
        return draft
    prompt = render(
        "answer",
        version=1,
        user_query=user_query,
        draft=draft,
        history_block=render_history_block(history),
    )
    try:
        text = await provider.chat(
            [{"role": "user", "content": prompt}], timeout_s=timeout_s
        )
    except AIProviderError:
        return draft
    return text.strip() or draft


__all__ = [
    "compose_answer",
    "format_answer",
    "format_check_existence",
    "format_describe_storage",
    "format_find_item",
    "format_find_items",
    "format_find_location",
    "format_list_category",
    "format_suggest_placement",
]
