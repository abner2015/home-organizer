"""Tests for the assistant's chat memory (``conversation_service``).

Real DB, no mocks: the point is that the ``conversations`` / ``messages``
tables — shipped in Phase 2 and unused until now — actually round-trip a
turn so the next request can see it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import NotFoundError
from app.models import Conversation, Message
from app.services import conversation_service
from tests.unit.conftest import StorageHierarchy  # noqa: F401  (fixture typing)

pytestmark = pytest.mark.asyncio


async def _session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    return factory()


async def test_first_turn_opens_a_conversation_with_no_history(
    seeded_actor, db_engine
) -> None:
    session = await _session(db_engine)
    try:
        ctx = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        assert ctx.history == ""
        row = (
            await session.execute(
                select(Conversation).where(Conversation.id == ctx.conversation_id)
            )
        ).scalar_one()
        assert row.home_id == seeded_actor.home_id
        assert row.user_id == seeded_actor.user_id
    finally:
        await session.close()


async def test_recorded_turn_is_replayed_on_the_next_turn(
    seeded_actor, db_engine
) -> None:
    """This is the whole point of the feature: turn 2 can read turn 1."""
    session = await _session(db_engine)
    try:
        first = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        await conversation_service.record_turn(
            session,
            conversation_id=first.conversation_id,
            user_query="我的数据线在哪里？",
            answer_text="数据线在 书房/书桌抽屉/第2格。",
        )
        await session.commit()

        second = await conversation_service.begin_turn(
            session,
            conversation_id=first.conversation_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        assert second.conversation_id == first.conversation_id
        assert "用户：我的数据线在哪里？" in second.history
        assert "助手：数据线在 书房/书桌抽屉/第2格。" in second.history
    finally:
        await session.close()


async def test_turn_is_stored_user_first_then_assistant(
    seeded_actor, db_engine
) -> None:
    session = await _session(db_engine)
    try:
        ctx = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        await conversation_service.record_turn(
            session,
            conversation_id=ctx.conversation_id,
            user_query="问题",
            answer_text="回答",
        )
        rows = (
            await session.execute(
                select(Message.role, Message.content)
                .where(Message.conversation_id == ctx.conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        ).all()
        assert [(r.role, r.content) for r in rows] == [
            ("user", "问题"),
            ("assistant", "回答"),
        ]
    finally:
        await session.close()


async def test_empty_answer_writes_only_the_user_message(
    seeded_actor, db_engine
) -> None:
    """An empty assistant reply must not create an unreadable blank bubble."""
    session = await _session(db_engine)
    try:
        ctx = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        await conversation_service.record_turn(
            session,
            conversation_id=ctx.conversation_id,
            user_query="问题",
            answer_text="   ",
        )
        rows = (
            await session.execute(
                select(Message.role).where(Message.conversation_id == ctx.conversation_id)
            )
        ).all()
        assert [r.role for r in rows] == ["user"]
    finally:
        await session.close()


async def test_unknown_conversation_id_is_404(seeded_actor, db_engine) -> None:
    session = await _session(db_engine)
    try:
        with pytest.raises(NotFoundError):
            await conversation_service.begin_turn(
                session,
                conversation_id=uuid.uuid4(),
                home_id=seeded_actor.home_id,
                user_id=seeded_actor.user_id,
            )
    finally:
        await session.close()


async def test_another_users_conversation_is_404(seeded_actor, db_engine) -> None:
    """No existence leak: a real conversation id that isn't yours reads as
    not-found, not forbidden."""
    session = await _session(db_engine)
    try:
        mine = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        await session.commit()

        with pytest.raises(NotFoundError):
            await conversation_service.begin_turn(
                session,
                conversation_id=mine.conversation_id,
                home_id=seeded_actor.home_id,
                user_id=uuid.uuid4(),  # someone else
            )
    finally:
        await session.close()


async def test_history_is_capped_so_a_long_chat_cannot_blow_up_the_prompt(
    seeded_actor, db_engine
) -> None:
    from app.agents.search.history import MAX_HISTORY_TURNS

    session = await _session(db_engine)
    try:
        ctx = await conversation_service.begin_turn(
            session,
            conversation_id=None,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        for i in range(MAX_HISTORY_TURNS + 3):
            await conversation_service.record_turn(
                session,
                conversation_id=ctx.conversation_id,
                user_query=f"第{i}问",
                answer_text=f"第{i}答",
            )
        await session.commit()

        later = await conversation_service.begin_turn(
            session,
            conversation_id=ctx.conversation_id,
            home_id=seeded_actor.home_id,
            user_id=seeded_actor.user_id,
        )
        assert "第0问" not in later.history
        assert f"第{MAX_HISTORY_TURNS + 2}答" in later.history
        assert later.history.count("\n") == MAX_HISTORY_TURNS - 1
    finally:
        await session.close()
