"""Search agent orchestrator (Phase 6).

The search agent is a *read-only* orchestrator: it accepts a natural-
language Chinese query, extracts the user's intent via the LLM, then
dispatches to one of seven item flows plus ``DESCRIBE_STORAGE``, all backed by
the existing :class:`~app.tools.registry.ToolRegistry`. It never persists a
``Recommendation`` row — only the ``AgentTrace`` row that wraps the
run. ``SUGGEST_PLACEMENT`` proposes a slot for a hypothetical item by
running the deterministic ranker in memory, and hands the user a CTA
into the real add-item flow rather than writing anything.
``DESCRIBE_STORAGE`` is the only flow that reads the storage hierarchy rather
than items — it is what makes 「我家有几个柜子？」 answerable.

The agent's contract:

- One LLM call (intent extraction), plus two read queries
  (:meth:`SearchAgent._build_context`) that ground the intent prompt in
  the caller's real categories and positions.
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

from app.agents.candidate_gen import generate_candidates, hard_filter
from app.agents.ranking import rank_slots
from app.agents.search.answer import (
    format_answer,
    format_describe_storage,
    format_suggest_placement,
)
from app.agents.search.context import build_home_context
from app.agents.search.intent import (
    ExtractedSearchIntent,
    SearchIntentKind,
    extract_intent,
)
from app.agents.search.location import match_slots_by_hint
from app.agents.search.structure import build_home_blueprint
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
    - ``suggested_slot`` / ``suggested_reason`` / ``suggested_item_name`` —
      only populated by the SUGGEST_PLACEMENT flow, which proposes where a
      *hypothetical* item should go. Nothing is written to the DB; the UI uses
      these to render a suggestion card whose CTA opens the real add-item flow.
    """

    intent: ExtractedSearchIntent
    matches: list[dict[str, Any]] = field(default_factory=list)
    answer_text: str = ""
    state: str = "answer"
    suggested_slot: dict[str, Any] | None = None
    suggested_reason: str = ""
    suggested_item_name: str = ""


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
        history: str = "",
    ) -> SearchRunResult:
        """Run the full search pipeline once. Returns a :class:`SearchRunResult`.

        ``history`` is the caller's prior-turn transcript (see
        :mod:`app.agents.search.history`); it is what lets a follow-up resolve
        its pronouns. Empty means first turn.

        Both the LLM call and the dispatch are wrapped: any
        :class:`AIProviderError` (transport / auth / quota / timeout) or
        unexpected DB failure collapses to ``state='error'`` so the API
        never has to surface a 5xx for a user query.
        """
        try:
            # 1. Ground the model in the caller's real home, then extract intent.
            home_context = await self._build_context(home_id=home_id)
            intent = await extract_intent(
                self._ai, user_query=query, home_context=home_context, history=history
            )

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
            if intent.intent == SearchIntentKind.SUGGEST_PLACEMENT:
                return await self._handle_suggest_placement(
                    home_id=home_id, user_id=user_id, intent=intent
                )
            if intent.intent == SearchIntentKind.DESCRIBE_STORAGE:
                return await self._handle_describe_storage(home_id=home_id, intent=intent)
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
        matched_slots = match_slots_by_hint(all_slots, hint)
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

    async def _handle_describe_storage(
        self, *, home_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """DESCRIBE_STORAGE: answer questions about the storage *structure*.

        This is the one flow that never touches ``Item`` rows. It reads the
        same four hierarchy tables the agent pipeline uses and renders them as a
        Chinese blueprint (counts + per-room breakdown), which
        :func:`app.services.search_service.run_search` then hands to the LLM to
        phrase as a count, a list or a capacity judgement — whichever the user
        actually asked. See :mod:`app.agents.search.structure`.

        ``intent.location_hint`` is deliberately *not* used to pre-filter:
        scoping 「客厅有几个柜子？」 is a language task the composer does better
        than a substring matcher, and the full overview is what makes
        「够不够用」 answerable.
        """
        rooms = await self._call(self._tools.get_rooms, home_id=home_id)
        if not rooms:
            fallback = ExtractedSearchIntent(
                intent=SearchIntentKind.DESCRIBE_STORAGE,
            )
            state, text = format_describe_storage(fallback, "")
            return SearchRunResult(
                intent=intent, matches=[], answer_text=text, state=state
            )

        units = await self._call(self._tools.get_storage_units, home_id=home_id)
        sections = await self._call(self._tools.get_storage_sections, home_id=home_id)
        slots = await self._call(self._tools.get_storage_slots, home_id=home_id)
        blueprint = build_home_blueprint(
            rooms=rooms, units=units, sections=sections, slots=slots
        )
        state, text = format_describe_storage(intent, blueprint.text)
        return SearchRunResult(
            intent=intent, matches=[], answer_text=text, state=state
        )

    async def _handle_suggest_placement(
        self, *, home_id: uuid.UUID, user_id: uuid.UUID, intent: ExtractedSearchIntent
    ) -> SearchRunResult:
        """SUGGEST_PLACEMENT: propose a slot for a *hypothetical* item.

        Read-only by design: the item is synthetic, so the deterministic
        generate → hard-filter → rank pipeline (the same one behind
        ``GET /items/{id}/candidates`` in ``app/api/v1/items.py``) runs in
        memory and nothing is persisted. The UI turns the result into a
        suggestion card with a CTA into the real add-item flow.
        """
        name = (intent.query or "").strip()
        category = (intent.category or "").strip()
        if not name and not category:
            fallback = ExtractedSearchIntent(
                intent=SearchIntentKind.UNKNOWN,
                clarification_needed=True,
                question="请问您想收纳的是什么物品？例如「雨伞」。",
            )
            return SearchRunResult(
                intent=fallback,
                matches=[],
                answer_text=fallback.question,
                state="needs_clarification",
            )

        item_name = name or category
        item: dict[str, Any] = {
            "id": None,
            "name": item_name,
            "category": category.lower(),
            # Size is unknown for a hypothetical item; "" is accepted by every
            # branch of ``candidate_gen._size_fits`` so it never over-restricts.
            "estimated_size": "",
            "is_sensitive": False,
            "needs_lock": False,
        }

        slots = await self._call(self._tools.get_storage_slots, home_id=home_id)
        rules = await self._call(self._tools.get_home_rules, home_id=home_id)
        preferences = await self._call(
            self._tools.get_user_preferences, home_id=home_id, user_id=user_id
        )

        generated = generate_candidates(slots, item)
        filtered = hard_filter(
            generated,
            item=item,
            hard_rules=[r for r in rules if r.get("rule_type") == "hard"],
            active_count={
                uuid.UUID(str(s["id"])): int(s.get("active_count") or 0) for s in generated
            },
        )
        ranked = rank_slots(
            filtered,
            item=item,
            preferences=preferences,
            # A hypothetical item has no placement history to match against.
            history=[],
            soft_rules=[r for r in rules if r.get("rule_type") == "soft"],
            limit=3,
        )

        top = ranked[0] if ranked else None
        state, text, reason = format_suggest_placement(
            intent, item_name=item_name, item_category=item["category"], slot=top
        )
        suggested_slot: dict[str, Any] | None = None
        if top is not None:
            # Deferred import: ``app.services`` pulls in ``app.agents.pipeline``,
            # which would cycle with ``app.agents.search`` at module load.
            from app.services.recommendation_service import candidate_view_from_slot

            suggested_slot = candidate_view_from_slot(top)

        return SearchRunResult(
            intent=intent,
            matches=[],
            answer_text=text,
            state=state,
            suggested_slot=suggested_slot,
            suggested_reason=reason,
            suggested_item_name=item_name,
        )

    # ------------------------------------------------------------------ helpers

    async def _build_context(self, *, home_id: uuid.UUID) -> str:
        """Render the caller's real home for the intent prompt.

        Two read-only queries; no LLM call. Grounding matters because the
        intent model otherwise invents category names (see
        :mod:`app.agents.search.context`).
        """
        slots = await self._call(self._tools.get_storage_slots, home_id=home_id)
        items = await self._call(self._tools.get_items, home_id=home_id)
        return build_home_context(slots=slots, items=items)

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
