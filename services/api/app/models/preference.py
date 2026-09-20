"""UserPreference — per-user, per-home key/value preferences (jsonb)."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class UserPreference(Base):
    """A key/value preference scoped to (user, home).

    The `value` JSON may hold any structured preference (theme, language, AI
    verbosity, default categories, etc.).
    """

    __tablename__ = "user_preferences"
    __table_args__ = (
        Index("uq_user_preferences_user_home_key", "user_id", "home_id", "key", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    home_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[dict[str, object]] = mapped_column(JSONBCompat, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserPreference user={self.user_id} home={self.home_id} key={self.key!r}>"
