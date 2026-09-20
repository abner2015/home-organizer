"""Asset — a generic uploaded blob (image, eventually anything binary).

An Asset is created on upload BEFORE the user has filled out an Item. The Item
may later reference one or more Assets via ItemImage.item_id. Keeping assets in
their own table allows presigned URLs to be issued independently and lets us
garbage-collect uploads that the user abandoned.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import GUID

if TYPE_CHECKING:
    from app.models.home import Home
    from app.models.user import User


class Asset(Base, TimestampMixin):
    """An uploaded binary object stored in MinIO/S3.

    The `object_key` is the canonical S3/MinIO key under which the bytes live.
    It is generated server-side — the original filename is never used as a key
    (security: filename injection, path traversal).
    """

    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_home_id_created", "home_id", "created_at"),
        Index("ix_assets_sha256", "sha256"),
        CheckConstraint(
            "status IN ('pending','ready','failed')",
            name="ck_assets_status",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_assets_size_non_negative",
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
    created_by: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bucket: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="MinIO/S3 bucket name"
    )
    object_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        unique=True,
        comment="Server-generated S3/MinIO object key",
    )
    content_type: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="MIME type, validated against whitelist"
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="Size of the uploaded body in bytes"
    )
    sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Hex SHA-256 of the uploaded body for dedup",
    )
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="Filename as sent by client; NEVER used as key"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending", default="pending"
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set when status transitions to 'ready'",
    )

    # Relationships
    home: Mapped[Home] = relationship(lazy="joined")
    uploader: Mapped[User] = relationship(foreign_keys=[created_by], lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset id={self.id} key={self.object_key!r} status={self.status}>"

