"""AIProvider Protocol and Pydantic output schemas.

This module is the **single boundary** between business code and any specific
LLM vendor. Concrete implementations live in ``app/ai/providers/<vendor>.py``
and are selected at runtime via the ``AI_PROVIDER`` env var.

The contract:

- Business code imports only the ``AIProvider`` Protocol and the Pydantic
  output schemas (``VisionOutput`` etc.). It must NEVER import a vendor SDK
  or a concrete provider class.
- Every method returns a strict Pydantic model (``extra="forbid"`` +
  ``strict=True``) — raw dicts are not allowed to leak.
- Errors are typed (see ``app.ai.errors``); the provider is responsible for
  classifying transport / parse / auth / quota / refused outcomes.
- Methods MUST NOT log API keys, image URLs, or raw prompt contents. They
  return data; observability is layered on by the service that calls them.

The interface follows the project prompt (AI vision_prompt.md):

    AIProvider supports (at minimum):
        - vision(image_url, hint, ...) -> VisionOutput
        - chat(messages, ...) -> str
        - structured_output(prompt, schema, ...) -> Pydantic model

``rank_candidates`` (used in the recommendation pipeline) is also declared
here so the same Protocol covers Phase 5 (Vision) and Phase 8 (Rank).
"""
from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- Vision output


class VisionOutput(BaseModel):
    """Structured output of a Vision LLM call.

    Per the project prompt (AI vision_prompt.md) — this is the minimum shape
    required for the first phase of item recognition. Additional provider-
    specific fields are NOT allowed; use ``strict=True`` + ``extra='forbid'``
    so a stray field from the LLM fails parsing rather than silently being
    dropped.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=128)
    # Blank is a *legal* answer: ``vision.v2.md`` tells the model to leave this
    # empty when nothing in the caller's real vocabulary fits, and the context
    # block says so outright for a home that has no storage yet. Requiring
    # min_length=1 turned that obedience into an ``AIOutputIncompleteError``, so
    # a brand-new user's first photo burned two retries and then failed. Kept in
    # sync with ``RecognitionResult``.
    category: str = Field(default="", max_length=64)
    subcategory: str = Field(default="", max_length=64)
    usage_scene: str = Field(default="", max_length=128)
    usage_frequency: Literal["high", "medium", "low"] = "medium"
    size_class: Literal["small", "medium", "large"] = "medium"
    fragility: Literal["low", "medium", "high"] = "low"
    notes: str = Field(default="", max_length=512)
    # Whether the item must be kept out of reach / under lock. Judged by the
    # model (see `vision_v2` prompt); defaults keep older callers and the mock
    # valid without a signature change.
    is_sensitive: bool = False
    needs_lock: bool = False


# --------------------------------------------------------------------------- item inference output


class ItemInferenceOutput(BaseModel):
    """Attributes guessed for a *typed* item name (no image involved).

    Backs ``POST /api/v1/items/infer``: the Web app hands over a name and the
    model fills the rest of the item form so the user only has to confirm.

    Not ``strict``, and every optional field is `str | None`: the prompt asks
    the model to leave a field empty when nothing fits, and JSON `null` is a
    likely spelling of "empty". A non-Optional field would turn that into an
    ``AIOutputParseError``. Callers normalise `None` to `""`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    subcategory: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=512)
    estimated_size: Literal["small", "medium", "large"] | None = None
    is_sensitive: bool = False
    needs_lock: bool = False


# --------------------------------------------------------------------------- Ranking output (Phase 8 stub)


class CandidateSlot(BaseModel):
    """A single ranked storage candidate produced by the Rank LLM call.

    ``extra="forbid"`` keeps stray LLM fields out, but the model is
    deliberately NOT ``strict``: this schema is validated against
    JSON-parsed LLM output, where ``slot_id`` / ``evidence_item_ids`` are
    strings. Pydantic's strict mode demands real ``UUID`` instances and
    rejects them (see MEMORY: strict + JSON = UUID strings rejected).
    """

    model_config = ConfigDict(extra="forbid")

    slot_id: UUID
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=512)
    matched_rules: list[str] = []
    evidence_item_ids: list[UUID] = []


class RankingOutput(BaseModel):
    """Structured output of a Rank LLM call (Phase 8).

    Not ``strict`` for the same reason as :class:`CandidateSlot` — it is
    validated against JSON text from the LLM.
    """

    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateSlot] = Field(min_length=1, max_length=3)


# --------------------------------------------------------------------------- Provider Protocol


@runtime_checkable
class AIProvider(Protocol):
    """Abstract interface for an AI backend.

    Implementations must:

    - Expose ``name`` (e.g. ``"openai_compatible"``).
    - Return strict Pydantic outputs from every method.
    - Raise ``AIProviderError`` subclasses (see ``app.ai.errors``) for any
      transport / 5xx / timeout / 4xx outcomes. Never raise bare
      ``Exception``.
    - NEVER log API keys, image URLs, or raw prompt contents. The provider
      may return raw text (e.g. ``chat()``) but MUST scrub obvious PII in
      higher-level services before any log emission.
    - Use the supplied ``timeout_s`` for every network call.
    """

    name: str

    # ----- Vision -------------------------------------------------------

    async def vision(
        self,
        image_url: str,
        *,
        hint: str | None = None,
        context: str = "",
        timeout_s: float = 30.0,
    ) -> VisionOutput:
        """Identify the item in ``image_url`` and return structured output.

        ``image_url`` is opaque to the provider — it may be a public URL, a
        presigned MinIO URL, or a ``data:`` URI. The provider decides how to
        fetch / encode it for its own backend.

        ``context`` is an optional grounding block (the caller's real category
        vocabulary and location names, see
        :func:`app.agents.search.context.build_home_context`). It is rendered
        into the prompt so the model does not invent a ``category`` that no
        storage slot accepts.
        """
        ...

    # ----- Chat ---------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        timeout_s: float = 30.0,
    ) -> str:
        """Free-form chat completion. Returns the assistant text only."""
        ...

    # ----- Structured output -------------------------------------------

    async def structured_output(
        self,
        prompt: str,
        schema: type[BaseModel],
        *,
        timeout_s: float = 30.0,
    ) -> BaseModel:
        """Generic structured-output call.

        ``schema`` is a Pydantic model class. The provider must force the LLM
        to conform to ``schema`` (e.g. via ``response_format`` or tool use)
        and return a parsed instance. On any parse failure it must raise
        ``AIOutputParseError`` (not return ``None`` / a dict).
        """
        ...

    # ----- Rank (Phase 8) ----------------------------------------------

    async def rank_candidates(
        self,
        *,
        item: dict[str, Any],
        candidates: list[dict[str, Any]],
        rules: list[dict[str, Any]],
        preferences: list[dict[str, Any]],
        history: list[dict[str, Any]],
        last_failure: str | None = None,
        timeout_s: float = 30.0,
    ) -> RankingOutput:
        """Rank a pre-filtered candidate list (Phase 8 of the pipeline)."""
        ...
