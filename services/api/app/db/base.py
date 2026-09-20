"""SQLAlchemy declarative base + shared mixins."""
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Timezone-aware UTC now (SQLAlchemy default)."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TimestampMixin:
    """Adds created_at / updated_at to a model.

    Apply to any model that needs row timestamps. Uses `func.current_timestamp()`
    as the server default so the schema works on SQLite (tests) and PostgreSQL
    (production). The Python `default` and `onupdate` keep values current
    even when SQLAlchemy inserts without an explicit value.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.current_timestamp(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.current_timestamp(),
        nullable=False,
    )
