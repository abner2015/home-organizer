"""Propose a storage structure from a photo, a sentence, or nothing at all.

The whole point of this batch: before it, the only way to create a room was
``python -m app.db.seed``, so a freshly registered account owned an empty tree
and every recommendation answered ``state="failed"``. This service turns
"here is my kitchen" into a proposal the user can confirm.

Three sources, one response shape:

- ``photo`` — the caller uploaded an image; its bytes are inlined as a
  ``data:`` URI and attached to the same structured-output call.
- ``text`` — a sentence ("我家厨房有个三层吊柜").
- ``template`` — no input at all. No LLM call, no ``AgentTrace``, no cost.

**Nothing here is persisted.** The only rows written are the observability
trace; the proposal exists in the response and nowhere else. Persisting is the
user's decision, expressed through the ordinary write endpoints.

Retry policy is the vision path's, copied rather than reused through
``vision_service.recognize_image`` because that function's output type is
pinned to ``VisionOutput``. The constants and the exception classification are
imported straight from it so the two cannot disagree about what is worth
retrying.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import render
from app.agents.structure.context import build_structure_context
from app.agents.structure.template import build_template_proposal
from app.agents.structure.validate import ProposalWarning, validate_proposal
from app.ai.observability import hash_prompt, timed
from app.ai.provider import AIProvider, StructureProposalOutput
from app.core.exceptions import NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.models import AgentTrace, Asset
from app.services.image_payload import to_data_uri
from app.services.vision_service import (
    MAX_PARSE_RETRIES,
    MAX_TRANSPORT_RETRIES,
    NON_RETRYABLE_ERRORS,
    RETRYABLE_PARSE_ERRORS,
    RETRYABLE_TRANSPORT_ERRORS,
)
from app.storage.backend import get_storage

logger = get_logger(__name__)

ProposalSource = Literal["photo", "text", "template"]

SOURCE_PHOTO: ProposalSource = "photo"
SOURCE_TEXT: ProposalSource = "text"
SOURCE_TEMPLATE: ProposalSource = "template"

STEP_TYPE = "structure_proposal"


@dataclass(slots=True)
class ProposalResult:
    """Outcome of one proposal run."""

    proposal: StructureProposalOutput
    warnings: list[ProposalWarning]
    source: ProposalSource
    #: LLM calls made. Zero for the template branch.
    attempts: int
    #: ``None`` on the template branch — no model was involved, so there is
    #: nothing to trace.
    trace_id: uuid.UUID | None


async def propose_structure(
    db: AsyncSession,
    *,
    provider: AIProvider,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    asset_id: uuid.UUID | None = None,
    description: str | None = None,
    timeout_s: float = 30.0,
) -> ProposalResult:
    """Propose a structure for ``home_id`` from a photo and/or a description.

    Raises:
        NotFoundError: ``asset_id`` names an asset outside the caller's home.
        ValidationFailedError: the asset exists but its bytes aren't ready.
        AIProviderError subclasses: bubbled up from the provider.
    """
    note = (description or "").strip()
    context = await build_structure_context(db, home_id=home_id)

    if asset_id is None and not note:
        # No input: the server-side template. Deliberately not an LLM call —
        # a working model must not be a prerequisite for building a home.
        proposal, warnings = validate_proposal(
            build_template_proposal(context.categories),
            vocabulary=context.categories,
            room_names=context.room_names,
            unit_names=context.unit_names,
        )
        return ProposalResult(
            proposal=proposal,
            warnings=warnings,
            source=SOURCE_TEMPLATE,
            attempts=0,
            trace_id=None,
        )

    if asset_id is not None:
        asset = await _load_asset(db, asset_id, home_id)
        image_url: str | None = to_data_uri(get_storage().get(asset.object_key))
        source = SOURCE_PHOTO
    else:
        image_url = None
        source = SOURCE_TEXT

    prompt = render(
        "structure",
        version=1,
        home_context=context.text,
        # A photo with no caption still needs the line to say something; the
        # image itself is attached by the provider, not by this string.
        input_note=note or "（没有文字描述，请只根据照片判断）",
    )
    prompt_hash = hash_prompt(prompt)

    steps: list[dict[str, object]] = []
    attempt = 0
    output: StructureProposalOutput | None = None

    while True:
        attempt += 1
        try:
            with timed() as get_ms:
                output = cast(
                    StructureProposalOutput,
                    await provider.structured_output(
                        prompt,
                        StructureProposalOutput,
                        image_url=image_url,
                        timeout_s=timeout_s,
                    ),
                )
            steps.append(
                _step_payload(
                    attempt=attempt,
                    provider=provider.name,
                    source=source,
                    prompt_hash=prompt_hash,
                    duration_ms=get_ms(),
                    parse_ok=True,
                    error=None,
                )
            )
            break
        except NON_RETRYABLE_ERRORS as exc:
            steps.append(_failed_step(attempt, provider.name, source, exc))
            logger.warning(
                "structure_proposal.non_retryable_error",
                provider=provider.name,
                source=source,
                error=type(exc).__name__,
                attempt=attempt,
            )
            await _persist_error_trace(
                db, steps=steps, home_id=home_id, user_id=user_id, error=exc
            )
            raise
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            steps.append(_failed_step(attempt, provider.name, source, exc))
            if attempt > MAX_TRANSPORT_RETRIES:
                logger.warning(
                    "structure_proposal.transport_exhausted",
                    provider=provider.name,
                    source=source,
                    error=type(exc).__name__,
                    attempts=attempt,
                )
                await _persist_error_trace(
                    db, steps=steps, home_id=home_id, user_id=user_id, error=exc
                )
                raise
        except RETRYABLE_PARSE_ERRORS as exc:
            steps.append(_failed_step(attempt, provider.name, source, exc))
            if attempt > MAX_PARSE_RETRIES:
                logger.warning(
                    "structure_proposal.parse_exhausted",
                    provider=provider.name,
                    source=source,
                    error=type(exc).__name__,
                    attempts=attempt,
                )
                await _persist_error_trace(
                    db, steps=steps, home_id=home_id, user_id=user_id, error=exc
                )
                raise

    assert output is not None

    proposal, warnings = validate_proposal(
        output,
        vocabulary=context.categories,
        room_names=context.room_names,
        unit_names=context.unit_names,
    )
    # Recorded on the successful step: "the model keeps proposing categories we
    # throw away" is only visible from here.
    steps[-1]["warnings"] = [warning.kind for warning in warnings]

    trace = AgentTrace(
        home_id=home_id,
        user_id=user_id,
        # A proposal is not about an item; nothing has been created yet.
        item_id=None,
        steps=steps,
        # Warnings are not failures — they are edits the confirm UI discloses.
        final_status="success",
        total_duration_ms=_total_duration_ms(steps),
        error=None,
    )
    db.add(trace)
    await db.flush()

    logger.info(
        "structure_proposal.success",
        provider=provider.name,
        source=source,
        attempts=attempt,
        rooms=len(proposal.rooms),
        warning_kinds=[warning.kind for warning in warnings],
    )
    return ProposalResult(
        proposal=proposal,
        warnings=warnings,
        source=source,
        attempts=attempt,
        trace_id=trace.id,
    )


# --------------------------------------------------------------- helpers


def _step_payload(
    *,
    attempt: int,
    provider: str,
    source: str,
    prompt_hash: str,
    duration_ms: int,
    parse_ok: bool,
    error: str | None,
) -> dict[str, object]:
    return {
        "step_type": STEP_TYPE,
        "attempt": attempt,
        "provider": provider,
        "source": source,
        "prompt_hash": prompt_hash,
        "duration_ms": duration_ms,
        "parse_ok": parse_ok,
        "error": error,
    }


def _total_duration_ms(steps: list[dict[str, object]]) -> int:
    """Sum the per-step durations.

    ``steps`` lands in a JSONB column, so mypy sees its values as ``object``;
    every value this module writes under ``duration_ms`` is an ``int`` (see
    ``_step_payload``). One cast, here, keeps it out of both call sites.
    """
    return sum(cast(int, step.get("duration_ms") or 0) for step in steps)


def _failed_step(
    attempt: int, provider: str, source: str, exc: BaseException
) -> dict[str, object]:
    return _step_payload(
        attempt=attempt,
        provider=provider,
        source=source,
        # Empty on failure, matching the vision service: there is no
        # successfully parsed call to correlate.
        prompt_hash="",
        duration_ms=0,
        parse_ok=False,
        error=type(exc).__name__,
    )


async def _persist_error_trace(
    db: AsyncSession,
    *,
    steps: list[dict[str, object]],
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    error: BaseException,
) -> None:
    """Record a failed run before re-raising.

    ``item_id`` is ``None`` for the same reason as the success path.
    """
    db.add(
        AgentTrace(
            home_id=home_id,
            user_id=user_id,
            item_id=None,
            steps=steps,
            final_status="error",
            total_duration_ms=_total_duration_ms(steps),
            error=f"{type(error).__name__}: {error}",
        )
    )
    await db.flush()


async def _load_asset(
    db: AsyncSession, asset_id: uuid.UUID, home_id: uuid.UUID
) -> Asset:
    """Load an asset the caller's home owns, or 404.

    Mirrors ``recognition_service._load_asset`` — same three-way outcome
    (ready / foreign / not ready), same statuses, so a client sees one
    behaviour for "photo I uploaded".
    """
    asset = (
        await db.execute(select(Asset).where(Asset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None or asset.home_id != home_id:
        raise NotFoundError("Asset not found")
    if asset.status != "ready":
        raise ValidationFailedError(
            "Asset is not ready for recognition",
            details={"status": asset.status},
        )
    return asset


__all__ = [
    "SOURCE_PHOTO",
    "SOURCE_TEMPLATE",
    "SOURCE_TEXT",
    "ProposalResult",
    "ProposalSource",
    "propose_structure",
]
