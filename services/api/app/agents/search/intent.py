"""Intent extraction for the natural-language search agent (Phase 6).

The search agent's first step is to figure out *what* the user is asking.
We delegate the parsing to the configured :class:`AIProvider` via
``provider.structured_output(prompt, schema=ExtractedSearchIntent)`` —
the same pattern the recognition service uses for vision output.

Hard rules from the project plan:

- Output must be a single Pydantic instance (no markdown, no prose).
- ``intent`` must be one of six enum values; ``UNKNOWN`` is the
  fall-back.
- When ``intent == UNKNOWN`` and ``clarification_needed`` is true,
  ``question`` MUST be a non-empty short Chinese follow-up.
- Any provider failure (``AIProviderError`` family) collapses to a
  well-formed ``UNKNOWN`` payload so callers never see raw exceptions.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent.prompts import render
from app.agents.search.history import render_history_block
from app.ai.errors import AIProviderError
from app.ai.provider import AIProvider

# ---------------------------------------------------------------------- enum


class SearchIntentKind(StrEnum):
    """Eight intents the search agent understands.

    The string values are persisted to the ``SearchResponse.intent`` field
    and surfaced to the UI — keep them stable across versions.

    ``DESCRIBE_STORAGE`` is the only *structure*-centric intent: the other
    seven all end up querying ``Item`` rows, so before it existed a question
    like 「我家有几个柜子？」 had no home and the model answered it by searching
    for an item named 柜子. See :mod:`app.agents.search.structure`.
    """

    FIND_ITEM = "find_item"
    FIND_ITEMS = "find_items"
    FIND_LOCATION = "find_location"
    CHECK_EXISTENCE = "check_existence"
    LIST_CATEGORY = "list_category"
    SUGGEST_PLACEMENT = "suggest_placement"
    DESCRIBE_STORAGE = "describe_storage"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------- payload


class ExtractedSearchIntent(BaseModel):
    """Structured intent the LLM extracts from a Chinese NL query.

    Field semantics:

    - ``intent`` — always one of :class:`SearchIntentKind`.
    - ``query`` — item-name hint (e.g. "数据线"); empty string if the user
      did not name a specific item. It must never hold a *structure* word
      (「柜子」/「房间」/「收纳空间」): those belong to ``DESCRIBE_STORAGE``, and
      ``search_items`` would substring-match them against item names and return
      whichever items happen to contain the character.
    - ``category`` — item-category hint, constrained by the prompt to values
      that actually exist in the caller's home (see
      :mod:`app.agents.search.context`); empty string if absent. It must never
      be a *room* name — ``search_items`` filters ``Item.category == category``
      exactly, so an invented value silently returns zero rows.
    - ``location_hint`` — room/unit/section/slot hint ("厨房" / "客厅装饰柜
      L1" …); empty string if absent. Also the optional scope of a
      ``DESCRIBE_STORAGE`` question ("客厅有几个柜子？").
    - ``clarification_needed`` — true when the LLM believes more user
      input is required to proceed.
    - ``question`` — short Chinese follow-up question to send back to
      the user when ``clarification_needed`` is true; empty string
      otherwise.

    ``extra="forbid"`` keeps the LLM honest. ``strict=True`` is *not*
    used (per project memory: it would reject string→UUID coercion in
    general, but here we don't accept UUIDs anyway — still, keeping the
    pattern consistent).
    """

    model_config = ConfigDict(extra="forbid")

    intent: SearchIntentKind
    query: str = Field(default="", max_length=128)
    category: str = Field(default="", max_length=64)
    location_hint: str = Field(default="", max_length=128)
    clarification_needed: bool = False
    question: str = Field(default="", max_length=256)


# ---------------------------------------------------------------------- fallback


def _fallback_unknown(reason: str = "无法理解您的问题") -> ExtractedSearchIntent:
    """Build a well-formed ``UNKNOWN`` payload for error / parse paths.

    We always return a fully-typed Pydantic instance so callers never have
    to handle a "missing intent" branch.
    """
    return ExtractedSearchIntent(
        intent=SearchIntentKind.UNKNOWN,
        query="",
        category="",
        location_hint="",
        clarification_needed=True,
        question=reason,
    )


# ---------------------------------------------------------------------- main call


async def extract_intent(
    provider: AIProvider,
    *,
    user_query: str,
    home_context: str = "",
    history: str = "",
) -> ExtractedSearchIntent:
    """Extract the user's intent via a single LLM structured call.

    ``home_context`` is the caller's rendered :func:`build_home_context` block —
    the user's real categories and storage positions. It is the only thing
    standing between the LLM and invented vocabulary (see
    :mod:`app.agents.search.context`); leaving it empty is supported for tests
    but makes the model guess.

    ``history`` is the prior-turn transcript rendered by
    :func:`app.agents.search.history.format_history`. It is what lets a
    follow-up ("那它放哪好？") resolve its pronouns; an empty string is
    signalled to the model as "first turn, assume no context".

    Handles two distinct failure modes:

    - **LLM responded but the output was unparseable** (``AIOutputParseError``
      or malformed Pydantic payload) → fall back to ``UNKNOWN`` with a
      user-friendly follow-up. The agent then returns
      ``state='needs_clarification'``.
    - **LLM could not be reached at all** (transport / auth / quota /
      timeout) → let it bubble up. The agent catches it and returns
      ``state='error'`` so the UI can show a system-failure message
      rather than pretending it was a user-input issue.
    """
    prompt = render(
        "search",
        4,
        user_query=user_query,
        home_context=home_context,
        history_block=render_history_block(history),
    )
    try:
        result = await provider.structured_output(
            prompt, schema=ExtractedSearchIntent
        )
    except AIProviderError as exc:
        # Re-raise transport / auth / quota / timeout / refused so the
        # agent surfaces them as ``state='error'``. Only parse errors
        # count as "the LLM tried but failed" → fall back to UNKNOWN.
        from app.ai.errors import AIOutputParseError

        if isinstance(exc, AIOutputParseError):
            return _fallback_unknown(reason="AI 返回的格式有误，请换一种说法。")
        raise

    if not isinstance(result, ExtractedSearchIntent):
        # The provider Protocol returns ``BaseModel``; coerce defensively.
        try:
            result = ExtractedSearchIntent.model_validate(result.model_dump())
        except Exception:
            return _fallback_unknown(reason="AI 返回的格式有误，请换一种说法。")

    # Cross-field invariant: when clarification_needed is True, the
    # question MUST be a non-empty Chinese string. If the LLM forgot,
    # synthesise a generic follow-up so the UI has something to show.
    if result.clarification_needed and not result.question.strip():
        return result.model_copy(
            update={"question": "请补充更多信息以便我帮您找到物品。"}
        )

    # If the LLM returned intent=UNKNOWN but did not request
    # clarification, force the flag so the UI surfaces a follow-up.
    if result.intent == SearchIntentKind.UNKNOWN and not result.clarification_needed:
        return result.model_copy(
            update={
                "clarification_needed": True,
                "question": result.question
                or "我没明白您的问题，请换一种说法。",
            }
        )

    return result


__all__ = ["ExtractedSearchIntent", "SearchIntentKind", "extract_intent"]
