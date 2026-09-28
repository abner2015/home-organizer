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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import render
from app.agents.structure.context import build_structure_context
from app.agents.structure.template import build_template_proposal
from app.agents.structure.validate import ProposalWarning, validate_proposal
from app.ai.observability import hash_prompt, timed
from app.ai.provider import AIProvider, StructureProposalOutput
from app.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.db.base import utc_now
from app.models import AgentTrace, Asset, StructureProposal
from app.services import structure_service
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
    """Outcome of one proposal run.

    As of P1.3 the proposal is **persisted** (status=pending) before this
    object is returned. ``proposal_id`` is the row's UUID; the route handler
    hands it back to the client so the user can ``accept`` / ``reject`` it
    later from ``/home/proposals``.
    """

    proposal: StructureProposalOutput
    warnings: list[ProposalWarning]
    source: ProposalSource
    #: LLM calls made. Zero for the template branch.
    attempts: int
    #: ``None`` on the template branch — no model was involved, so there is
    #: nothing to trace.
    trace_id: uuid.UUID | None
    proposal_id: uuid.UUID


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
        row = await _persist_proposal(
            db,
            home_id=home_id,
            user_id=user_id,
            source=SOURCE_TEMPLATE,
            asset_id=None,
            description=None,
            proposal=proposal,
            warnings=warnings,
            trace_id=None,
        )
        return ProposalResult(
            proposal=proposal,
            warnings=warnings,
            source=SOURCE_TEMPLATE,
            attempts=0,
            trace_id=None,
            proposal_id=row.id,
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
    row = await _persist_proposal(
        db,
        home_id=home_id,
        user_id=user_id,
        source=source,
        asset_id=asset_id,
        description=description,
        proposal=proposal,
        warnings=warnings,
        trace_id=trace.id,
    )
    return ProposalResult(
        proposal=proposal,
        warnings=warnings,
        source=source,
        attempts=attempt,
        trace_id=trace.id,
        proposal_id=row.id,
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


# ----------------------------------------------------------- persistence (P1.3)


async def _persist_proposal(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
    source: ProposalSource,
    asset_id: uuid.UUID | None,
    description: str | None,
    proposal: StructureProposalOutput,
    warnings: list[ProposalWarning],
    trace_id: uuid.UUID | None,
) -> StructureProposal:
    """Write the pending proposal row and flush.

    The route handler's :func:`AsyncSession.commit` covers both this row and
    the just-written ``AgentTrace``; we only ``flush`` here so the
    proposal's id is available before :func:`propose_structure` returns.
    """
    row = StructureProposal(
        home_id=home_id,
        user_id=user_id,
        source=source,
        asset_id=asset_id,
        description=description,
        # ``proposal.rooms`` etc. are Pydantic models; the JSONBCompat column
        # wants plain dicts. ``model_dump`` is the cheapest way to round-trip.
        proposal=proposal.model_dump(mode="json"),
        warnings=[w.model_dump(mode="json") for w in warnings],
        trace_id=trace_id,
        status="pending",
    )
    db.add(row)
    await db.flush()
    return row


# --------------------------------------------------------------- read / list


async def get_proposal(
    db: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    home_id: uuid.UUID,
) -> StructureProposal:
    """Load a single proposal owned by ``home_id`` or 404.

    Cross-home access is 404, matching the project's "you can't even see that
    it exists" policy (see ``docs/AGENT.md`` and ``Recommendation`` reads).
    """
    row = (
        await db.execute(
            select(StructureProposal).where(StructureProposal.id == proposal_id)
        )
    ).scalar_one_or_none()
    if row is None or row.home_id != home_id:
        raise NotFoundError("Proposal not found")
    return row


async def list_proposals(
    db: AsyncSession,
    *,
    home_id: uuid.UUID,
    status: str | None = None,
) -> list[StructureProposal]:
    """List proposals owned by ``home_id``, newest first.

    ``status`` is an optional filter (``pending`` / ``accepted`` / ``rejected``
    / ``superseded``). No filter returns everything; the UI defaults to
    ``pending`` because accepted / rejected rows are inert.
    """
    stmt = select(StructureProposal).where(StructureProposal.home_id == home_id)
    if status is not None:
        stmt = stmt.where(StructureProposal.status == status)
    stmt = stmt.order_by(StructureProposal.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------- accept / reject


@dataclass(slots=True)
class AcceptOutcome:
    """Result of accepting a proposal — what got created and the row state."""

    proposal: StructureProposal
    counts: dict[str, int]


async def accept_proposal(
    db: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AcceptOutcome:
    """Materialise a pending proposal as real rooms/units/sections/slots.

    One transaction — if any slot code collides with an existing row (or a
    duplicate within the proposal itself), ``IntegrityError`` / ``ConflictError``
    rolls the whole batch back and the home is left untouched. The user can
    then reject this proposal and re-propose.
    """
    proposal = await get_proposal(db, proposal_id=proposal_id, home_id=home_id)
    if proposal.status != "pending":
        raise ConflictError(
            f"提议状态为 {proposal.status}，不能重复 accept",
            details={"proposal_id": str(proposal.id), "status": proposal.status},
        )

    counts = {"rooms": 0, "units": 0, "sections": 0, "slots": 0}
    try:
        proposal_data = cast(dict[str, object], proposal.proposal)
        rooms = cast(list[dict[str, object]], proposal_data.get("rooms") or [])
        for room in rooms:
            new_room = await structure_service.create_room(
                db,
                home_id=home_id,
                name=str(room["name"]),
                room_type=str(room["room_type"]),
            )
            counts["rooms"] += 1
            for unit in cast(list[dict[str, object]], room.get("units") or []):
                new_unit = await structure_service.create_unit(
                    db,
                    home_id=home_id,
                    room_id=new_room.id,
                    name=str(unit["name"]),
                    unit_type=str(unit["unit_type"]),
                )
                counts["units"] += 1
                for section in cast(
                    list[dict[str, object]], unit.get("sections") or []
                ):
                    new_section = await structure_service.create_section(
                        db,
                        home_id=home_id,
                        unit_id=new_unit.id,
                        name=str(section["name"]),
                        section_type=str(section["section_type"]),
                    )
                    counts["sections"] += 1
                    for slot in cast(
                        list[dict[str, object]], section.get("slots") or []
                    ):
                        await structure_service.create_slot(
                            db,
                            home_id=home_id,
                            section_id=new_section.id,
                            code=str(slot["code"]),
                            label=(
                                str(slot["label"])
                                if slot.get("label") is not None
                                else None
                            ),
                            capacity_hint=(
                                str(slot["capacity_hint"])
                                if slot.get("capacity_hint") is not None
                                else None
                            ),
                            allowed_categories=cast(
                                list[str] | None, slot.get("allowed_categories")
                            ),
                        )
                        counts["slots"] += 1
        proposal.status = "accepted"
        proposal.accepted_at = utc_now()
        await db.commit()
        await db.refresh(proposal)
        logger.info(
            "structure_proposal.accepted",
            proposal_id=str(proposal.id),
            home_id=str(home_id),
            **counts,
        )
        return AcceptOutcome(proposal=proposal, counts=counts)
    except ConflictError:
        await db.rollback()
        raise
    except IntegrityError as exc:
        # Belt-and-braces: ``create_slot`` already raises ``ConflictError`` for
        # duplicate (section_id, code); the partial unique index on PG will
        # surface here for races we cannot catch in Python (two accepts).
        await db.rollback()
        raise ConflictError(
            "提议里有 slot code 与现有冲突",
            details={"proposal_id": str(proposal.id)},
        ) from exc


async def reject_proposal(
    db: AsyncSession,
    *,
    proposal_id: uuid.UUID,
    home_id: uuid.UUID,
    note: str | None = None,
) -> StructureProposal:
    """Mark a pending proposal rejected. 409 if not pending."""
    proposal = await get_proposal(db, proposal_id=proposal_id, home_id=home_id)
    if proposal.status != "pending":
        raise ConflictError(
            f"提议状态为 {proposal.status}，不能 reject",
            details={"proposal_id": str(proposal.id), "status": proposal.status},
        )
    proposal.status = "rejected"
    proposal.rejected_at = utc_now()
    proposal.rejection_note = (note or "").strip() or None
    await db.commit()
    await db.refresh(proposal)
    logger.info(
        "structure_proposal.rejected",
        proposal_id=str(proposal.id),
        home_id=str(home_id),
    )
    return proposal


__all__ = [
    "SOURCE_PHOTO",
    "SOURCE_TEMPLATE",
    "SOURCE_TEXT",
    "AcceptOutcome",
    "ProposalResult",
    "ProposalSource",
    "accept_proposal",
    "get_proposal",
    "list_proposals",
    "propose_structure",
    "reject_proposal",
]
