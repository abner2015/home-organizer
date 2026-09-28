"""StructureProposal — a persisted AI structure draft (P1.3).

Why this table exists
---------------------

Before P1.3 the proposal was returned by ``POST /structures/propose`` and
**never persisted**. The frontend ``ProposalFlow.tsx`` then walked the tree
and issued one POST per node to materialise it. A partial failure (e.g. a
duplicate slot code) left the home with half a tree and forced the user to
retry the rest.

P1.3 persists the proposal so:

- the user has a list of drafts to review later (``/home/proposals``)
- ``accept_proposal`` runs as one transaction — all-or-nothing
- reject / superseded states become queryable for future UI work

**Pending proposals are inert** — they do not appear in
``GET /homes/{id}/space-tree``, do not feed candidate generation, and do not
match any search. Only ``accepted`` produces real ``rooms/units/sections/slots``
rows. This preserves the original intent of ``AGENTS.md`` §3.3: AI cannot
silently create structure. The persisted proposal is just a draft the user
can review.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class StructureProposal(Base):
    """A structure proposal produced by ``POST /structures/propose``.

    The recursive ``proposal`` JSON mirrors :class:`StructureProposalOutput`
    (rooms → units → sections → slots). ``accept_proposal`` walks this tree
    and calls the existing ``create_room`` / ``create_unit`` /
    ``create_section`` / ``create_slot`` services in a single transaction.
    """

    __tablename__ = "structure_proposals"
    __table_args__ = (
        Index("ix_structure_proposals_home_id", "home_id"),
        Index("ix_structure_proposals_status", "status"),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','superseded')",
            name="ck_structure_proposals_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    home_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="The user who generated this proposal.",
    )
    source: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="photo | text | template",
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Free-text description the user gave the model (text source).",
    )
    proposal: Mapped[list[dict[str, object]]] = mapped_column(
        JSONBCompat,
        nullable=False,
        comment="Recursive 4-level tree: rooms[*].units[*].sections[*].slots[*]",
    )
    warnings: Mapped[list[dict[str, object]]] = mapped_column(
        JSONBCompat,
        nullable=False,
        comment="Step-4 rewrites the model made (truncation, dedup, etc.)",
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("agent_traces.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="pending",
    )
    rejection_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<StructureProposal id={self.id} home={self.home_id} "
            f"source={self.source} status={self.status}>"
        )
