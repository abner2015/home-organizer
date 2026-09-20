"""AgentTrace — one full execution of the 9-step recommendation pipeline."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class AgentTrace(Base):
    """A full trace of one recommendation run.

    Every LLM call and every pipeline step is recorded as a single JSONB
    `steps` blob. `final_status` indicates the terminal state of the run
    (success / verifier_failed / error). Token usage and cost are recorded
    so we can later budget the AI spend.
    """

    __tablename__ = "agent_traces"
    __table_args__ = (
        Index("ix_agent_traces_item_id", "item_id"),
        Index("ix_agent_traces_home_id_created", "home_id", "created_at"),
        CheckConstraint(
            "final_status IN ('success','verifier_failed','error')",
            name="ck_agent_traces_final_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    home_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
    )
    steps: Mapped[list[dict[str, object]]] = mapped_column(
        JSONBCompat,
        nullable=False,
        comment="Pipeline step log: [{step, started_at, ended_at, payload, error}, ...]",
    )
    final_status: Mapped[str] = mapped_column(Text, nullable=False)
    total_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AgentTrace id={self.id} home={self.home_id} "
            f"status={self.final_status} duration={self.total_duration_ms}ms>"
        )
