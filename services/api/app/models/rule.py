"""HomeRule — household storage rules enforced by the agent pipeline."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class HomeRule(Base):
    """A natural-language storage rule, optionally scoped via JSON.

    `rule_type`:
      - "hard"  → must be enforced by Verifier; failure aborts recommendation
      - "soft"  → used as a ranking hint by the scoring step

    `scope` is an arbitrary JSONB describing applicability, e.g.
    {"room_types": ["kitchen"], "categories": ["food"]}.
    """

    __tablename__ = "home_rules"
    __table_args__ = (
        Index("ix_home_rules_home_id_enabled", "home_id", "enabled"),
        CheckConstraint(
            "rule_type IN ('hard','soft')",
            name="ck_home_rules_rule_type",
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
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    rule_type: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(
        JSONBCompat, nullable=False, default=dict, server_default=text("'{}'")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HomeRule id={self.id} name={self.name!r} type={self.rule_type}>"
