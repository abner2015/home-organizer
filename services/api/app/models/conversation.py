"""Conversation + Message — chat history for the agent UI.

Phase 2 ships the schema only; business logic arrives in a later phase.
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now
from app.db.types import GUID, JSONBCompat


class Conversation(Base):
    """A chat session between a user and the agent.

    Optionally linked to a specific Item (e.g. "where should I put this?") so
    the agent can load prior context.
    """

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_id", "user_id"),)

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
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey("items.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Conversation id={self.id} user={self.user_id} item={self.item_id}>"


class Message(Base):
    """A single message in a Conversation."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id", "created_at"),
        CheckConstraint(
            "role IN ('user','assistant','tool')",
            name="ck_messages_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        primary_key=True,
        default=uuid.uuid4,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, object]] | None] = mapped_column(JSONBCompat, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.current_timestamp(),
    )

    # Relationships
    conversation: Mapped[Conversation] = relationship(back_populates="messages", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Message id={self.id} role={self.role} conv={self.conversation_id}>"
