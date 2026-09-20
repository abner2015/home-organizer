"""Search agent orchestrator (Phase 6).

The search agent is a *read-only* orchestrator: it accepts a natural-
language Chinese query, extracts the user's intent via the LLM, then
dispatches to one of six concrete flows backed by the existing
:class:`~app.tools.registry.ToolRegistry`. It never persists a
``Recommendation`` row — only the ``AgentTrace`` row that wraps the
run.

The agent's contract:

- One LLM call (intent extraction). The DB tools handle the rest.
- Read-only against the DB. State is a single ``SearchRunResult``.
- Cross-home access is implicit because all tools filter by ``home_id``.
- If the LLM call fails, ``extract_intent`` falls back to ``UNKNOWN``
  and we return ``needs_clarification`` with a user-friendly follow-up.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search.answer import format_answer
from app.agents.search.intent import (
    ExtractedSearchIntent,
    SearchIntentKind,
    extract_intent,
)
from app.ai.provider import AIProvider
from app.tools.registry import ToolRegistry

# ---------------------------------------------------------------------- limits


# Hard caps to prevent pathologically large responses.
MAX_CANDIDATES_RETURNED = 10
MAX_LOCATION_MATCHES = 10


# ---------------------------------------------------------------------- result


@dataclass(slots=True)
class SearchRunResult:
    """Final result of one ``SearchAgent.run`` call.

    Fields:

    - ``intent`` — the parsed :class:`ExtractedSearchIntent`. Always
      present, even on ``state="error"`` (then it's ``UNKNOWN``).
    - ``matches`` — candidate items with their location. ``location``
      is ``None`` for items that exist in the DB but have no active
      placement (the API surfaces this so the UI can say "已找到但
      还没放好"). Capped at 10 entries.
    - ``answer_text`` — pre-formatted Chinese sentence for the UI.
      Always present (the API spec says the UI should render this
      verbatim, even on errors).
    - ``state`` — one of ``"answer"``, ``"needs_clarification"``,
      ``"not_found"``, ``"exists_but_not_placed"``, ``"error"``.
    """

    intent: ExtractedSearchIntent
    matches: list[dict[str, Any]] = field(default_factory=list)
    answer_text: str = ""
    state: str = "answer"


# ---------------------------------------------------------------------- candidate shape


def _candidate_dict(item: dict[str, Any], location: dict[str, Any] | None) -> dict[str, Any]:
    """Project an item + location into the search response candidate shape."""
    return {
        "item_id": uuid.UUID(item["id"]) if isinstance(item.get("id"), str) else item["id"],
        "name": item.get("name", ""),
        "category": item.get("category", ""),
        "subcategory": item.get("subcategory", ""),
        "is_sensitive": bool(item.get("is_sensitive", False)),
        "location": location,
    }


def _slot_ref(slot: dict[str, Any]) -> dict[str, Any]:
    """Project a slot dict (from :func:`home_tools.get_storage_slots`) into a
    :class:`app.schemas.search.SlotRef`-shaped dict."""
    return {
        "slot_id": uuid.UUID(slot["id"]) if isinstance(slot.get("id"), str) else slot["id"],
        "code": slot.get("code", ""),
        "label": slot.get("label", ""),
        "room_name": slot.get("room_name", ""),
        "unit_name": slot.get("unit_name", ""),
        "section_name": slot.get("section_name", ""),
        "full_path": slot.get("full_path", ""),
    }


def _slot_match_label(slot: dict[str, Any]) -> str:
    """Short Chinese label for a matched slot (used by FIND_LOCATION)."""
    return str(slot.get("full_path") or slot.get("label") or slot.get("code") or "")


# ---------------------------------------------------------------------- agent


class SearchAgent:
    """Read-only orchestrator that turns a Chinese query into a final answer."""

    def __init__(
        self,
        *,
        ai: AIProvider,
        tools: ToolRegistry,
        db: AsyncSession,
    ) -> None:
        self._ai = ai
        self._tools = tools
        self._db = db

    # ------------------------------------------------------------------ main

    async def run(
        self,
        *,
        home_id: uuid.UUID,
        user_id: uuid.UUID,  # kept for parity with the API surface; not used
        query: str,
    ) -> SearchRunResult:
        """Run the full search pipeline once. Returns a :class:`SearchRunResult`.

        Both the LLM call and the dispatch are wrapped: any
        :class:`AIProviderError` (transport / auth / quota / timeout) or
        unexpected DB failure collapses to ``state='error'`` so the API
        never has to surface a 5xx for a user query.
        """
        try:
            # 1. Intent extraction.
            intent = await extract_intent(self._ai, user_query=query)

            # 2. Dispatch by intent kind.
            if intent.intent == SearchIntentKind.FIND_ITEM:
                return await self._handle_find_item(home_id=home_id, intent=intent)
            if intent.intent == SearchIntentKind.FIND_ITEMS:
                return await self._handle_find_items(home_id=home_id, intent=intent)
            if intent.intent == SearchIntentKind.FIND_LOCATION:
                return await self._handle_find_location(home_id=home_id, intent=intent)
            if intent.intent == SearchIntentKind.CHECK_EXISTENCE:
                return await self._handle_check_existence(home_id=home_id, intent=intent)
            if intent.intent == SearchIntentKind.LIST_CATEGORY:
                return await self._handle_list_category(home_id=home_id, intent=intent)
        except Exception:
            # Convert DB / LLM errors into a well-formed "error" result.
            error_intent = ExtractedSearchIntent(
                intent=SearchIntentKind.UNKNOWN,
                clarification_needed=True,
                question="查询过程中出错，请稍后再试。",
            )
            return SearchRunResult(
                intent=error_intent,
                matches=[],
                answer_text="查询过程中出错，请稍后再试。",
                state="error",
            )

        # 3. UNKNOWN / fallback: surface the LLM-provided clarification question.
        state, text = format_answer(intent, [])
        return SearchRunResult(
            intent=intent,
            matches=[],
            answer_text=text,
            state=state,
        )

    # ------------------------------------------------------------------ handlers

    async def _handle_find_item(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """FIND_ITEM: resolve the item, attach its current location (or None)."""
        items = await self._call(
            self._tools.search_items,
            home_id=home_id,
            query=intent.query or None,
            category=intent.category or None,
        )
        # Cap to keep responses bounded.
        items = items[:MAX_CANDIDATES_RETURNED]
        candidates: list[dict[str, Any]] = []
        for item in items:
            placements = await self._call(
                self._tools.get_item_placements,
                home_id=home_id,
                item_id=uuid.UUID(item["id"]),
                active_only=True,
            )
            location: dict[str, Any] | None = None
            if placements:
                slot_id = placements[0]["slot_id"]
                slots = await self._call(
                    self._tools.get_storage_slots, home_id=home_id
                )
                # Find the matching slot by id; we keep the loop cheap because
                # we expect ≤ a few dozen slots per home.
                match = next((s for s in slots if s["id"] == slot_id), None)
                if match is not None:
                    location = _slot_ref(match)
            candidates.append(_candidate_dict(item, location))
        state, text = format_answer(intent, candidates)
        return SearchRunResult(
            intent=intent, matches=candidates, answer_text=text, state=state
        )

    async def _handle_find_items(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """FIND_ITEMS: list-mode — return all matches with their locations."""
        kwargs: dict[str, Any] = {
            "home_id": home_id,
            "query": intent.query or None,
            "category": intent.category or None,
        }
        if intent.location_hint:
            kwargs["room_name"] = intent.location_hint
        items = await self._call(self._tools.search_items, **kwargs)
        items = items[:MAX_CANDIDATES_RETURNED]
        # Build a slot index once for the N+1 placement/slot lookups.
        slot_index = await self._build_slot_index(home_id)
        candidates: list[dict[str, Any]] = []
        for item in items:
            placements = await self._call(
                self._tools.get_item_placements,
                home_id=home_id,
                item_id=uuid.UUID(item["id"]),
                active_only=True,
            )
            location: dict[str, Any] | None = None
            if placements:
                location = slot_index.get(placements[0]["slot_id"])
            candidates.append(_candidate_dict(item, location))
        state, text = format_answer(intent, candidates)
        return SearchRunResult(
            intent=intent, matches=candidates, answer_text=text, state=state
        )

    async def _handle_find_location(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """FIND_LOCATION: find slots whose label/path matches ``location_hint``,
        then enumerate items currently placed in those slots."""
        hint = (intent.location_hint or "").strip()
        if not hint:
            # No hint → fall back to UNKNOWN so the user is asked to be specific.
            fallback = ExtractedSearchIntent(
                intent=SearchIntentKind.UNKNOWN,
                clarification_needed=True,
                question="请问您想查看哪个位置？例如「客厅装饰柜L1」。",
            )
            return SearchRunResult(
                intent=fallback,
                matches=[],
                answer_text="请问您想查看哪个位置？例如「客厅装饰柜L1」。",
                state="needs_clarification",
            )
        all_slots = await self._call(self._tools.get_storage_slots, home_id=home_id)
        matched_slots = [
            s for s in all_slots
            if hint in (s.get("label") or "")
            or hint in (s.get("full_path") or "")
            or hint in (s.get("code") or "")
        ]
        if not matched_slots:
            state, text = format_answer(intent, [], slot_label=hint)
            return SearchRunResult(
                intent=intent, matches=[], answer_text=text, state=state
            )
        # Enumerate active placements in those slots.
        candidates: list[dict[str, Any]] = []
        slot_label = _slot_match_label(matched_slots[0])
        for slot in matched_slots[:MAX_LOCATION_MATCHES]:
            placements = await self._call(
                self._tools.get_item_placements,
                home_id=home_id,
                slot_id=uuid.UUID(slot["id"]),
                active_only=True,
            )
            for p in placements[:MAX_CANDIDATES_RETURNED - len(candidates)]:
                items = await self._call(
                    self._tools.get_items,
                    home_id=home_id,
                    item_id=uuid.UUID(p["item_id"]),
                )
                if not items:
                    continue
                candidates.append(
                    _candidate_dict(items[0], _slot_ref(slot))
                )
                if len(candidates) >= MAX_CANDIDATES_RETURNED:
                    break
            if len(candidates) >= MAX_CANDIDATES_RETURNED:
                break
        state, text = format_answer(intent, candidates, slot_label=slot_label)
        return SearchRunResult(
            intent=intent, matches=candidates, answer_text=text, state=state
        )

    async def _handle_check_existence(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """CHECK_EXISTENCE: substring match on ``query``, attach first location."""
        items = await self._call(
            self._tools.search_items,
            home_id=home_id,
            query=intent.query or None,
            category=intent.category or None,
        )
        items = items[:MAX_CANDIDATES_RETURNED]
        slot_index = await self._build_slot_index(home_id)
        candidates: list[dict[str, Any]] = []
        for item in items:
            placements = await self._call(
                self._tools.get_item_placements,
                home_id=home_id,
                item_id=uuid.UUID(item["id"]),
                active_only=True,
            )
            location: dict[str, Any] | None = None
            if placements:
                location = slot_index.get(placements[0]["slot_id"])
            candidates.append(_candidate_dict(item, location))
        state, text = format_answer(intent, candidates)
        return SearchRunResult(
            intent=intent, matches=candidates, answer_text=text, state=state
        )

    async def _handle_list_category(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """LIST_CATEGORY: items by category (or all items if category is absent),
        grouped by room in the answer."""
        kwargs: dict[str, Any] = {"home_id": home_id}
        if intent.category:
            kwargs["category"] = intent.category
        items = await self._call(self._tools.search_items, **kwargs)
        items = items[:MAX_CANDIDATES_RETURNED]
        slot_index = await self._build_slot_index(home_id)
        candidates: list[dict[str, Any]] = []
        for item in items:
            placements = await self._call(
                self._tools.get_item_placements,
                home_id=home_id,
                item_id=uuid.UUID(item["id"]),
                active_only=True,
            )
            location: dict[str, Any] | None = None
            if placements:
                location = slot_index.get(placements[0]["slot_id"])
            candidates.append(_candidate_dict(item, location))
        state, text = format_answer(intent, candidates)
        return SearchRunResult(
            intent=intent, matches=candidates, answer_text=text, state=state
        )

    # ------------------------------------------------------------------ helpers

    async def _build_slot_index(self, home_id: uuid.UUID) -> dict[str, dict[str, Any]]:
        """Pre-fetch all slots for the home once, returning slot_id → SlotRef."""
        slots = await self._call(self._tools.get_storage_slots, home_id=home_id)
        out: dict[str, dict[str, Any]] = {}
        for s in slots:
            out[s["id"]] = _slot_ref(s)
        return out

    @staticmethod
    async def _call(tool: Any, /, **kwargs: Any) -> Any:
        """Await a tool function — ``ToolFn`` is typed as ``Callable[..., object]``
        to keep :class:`ToolRegistry` flexible; this wrapper keeps the call
        site readable without sprinkling ``# type: ignore`` everywhere.
        """
        return await tool(**kwargs)


__all__ = ["MAX_CANDIDATES_RETURNED", "SearchAgent", "SearchRunResult"]
