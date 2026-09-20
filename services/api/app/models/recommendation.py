"""Recommendation — a persisted AI recommendation outcome."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class Recommendation(Base):
    """A single recommendation produced by the agent pipeline.

    Always references an AgentTrace (the run that produced it). The
    `candidates` JSONB contains the Top 1-3 candidate slots with scores and
    reasons from the 9-step pipeline (see docs/AGENT.md).
    """

    __tablename__ = "recommendations"
    __table_args__ = (
        Index("ix_recommendations_item_id", "item_id"),
        Index("ix_recommendations_status", "status"),
        CheckConstraint(
            "status IN ('pending','accepted','rejected','superseded')",
            name="ck_recommendations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("items.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_trace_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("agent_traces.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidates: Mapped[list[dict[str, object]]] = mapped_column(
        JSONBCompat,
        nullable=False,
        comment="Top 1-3 candidate slots with score + reason",
    )
    pre_filter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    post_filter_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    chosen_slot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("storage_slots.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Recommendation id={self.id} item={self.item_id} "
            f"chosen={self.chosen_slot_id} status={self.status}>"
        )
