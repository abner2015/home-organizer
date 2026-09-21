"""Chat memory for the 收纳助手.

The ``conversations`` / ``messages`` tables shipped in Phase 2 but nothing
wrote to them, so ``POST /api/v1/search`` was stateless: a follow-up like
「那它放哪好？」 had no referent and the assistant answered something else.

This service owns the two things the search endpoint needs:

1. :func:`begin_turn` — resolve the caller's conversation (or open a new one)
   and render its prior messages into a prompt-ready transcript.
2. :func:`record_turn` — append the user question and the assistant's final
   answer so the *next* turn can see them.

Callers commit; this module only adds/flushes (same convention as
``search_service``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.search.history import MAX_HISTORY_TURNS, format_history
from app.core.exceptions import NotFoundError
from app.models import Conversation, Message


@dataclass(slots=True)
class ConversationContext:
    """The conversation a turn belongs to, plus its rendered transcript."""

    conversation_id: uuid.UUID
    history: str


async def begin_turn(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID | None,
    home_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ConversationContext:
    """Resolve the conversation for this turn and load its transcript.

    ``conversation_id=None`` opens a fresh conversation (the first turn).
    An id that doesn't exist, belongs to another home, or belongs to another
    user raises :class:`NotFoundError` — 404 rather than 403, matching the
    project-wide no-existence-leak convention.

    Only the most recent :data:`MAX_HISTORY_TURNS` messages are replayed.
    """
    if conversation_id is None:
        conversation = Conversation(home_id=home_id, user_id=user_id)
        db.add(conversation)
        await db.flush()
        return ConversationContext(conversation_id=conversation.id, history="")

    existing = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.home_id == home_id,
                Conversation.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        raise NotFoundError("对话不存在", details={"conversation_id": str(conversation_id)})

    # Newest-last window: order by created_at desc, take N, then flip back so
    # the transcript reads chronologically.
    rows = (
        (
            await db.execute(
                select(Message.role, Message.content)
                .where(Message.conversation_id == existing.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(MAX_HISTORY_TURNS)
            )
        )
        .all()
    )
    history = format_history([(role, content) for role, content in reversed(rows)])
    return ConversationContext(conversation_id=existing.id, history=history)


async def record_turn(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    user_query: str,
    answer_text: str,
) -> None:
    """Append the user's question and the assistant's answer to the log.

    Both rows share one ``flush``; the caller's commit makes them durable.
    """
    db.add(
        Message(
            conversation_id=conversation_id,
            role="user",
            content=user_query,
        )
    )
    if answer_text.strip():
        db.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer_text,
            )
        )
    await db.flush()


__all__ = ["ConversationContext", "begin_turn", "record_turn"]
