import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent import ConversationNotFound, ConversationStore
from app.db.base import Base
from app.models import ChatMessage, ChatSession, User


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            User(id="user-a", username="a", email="a@example.test", password_hash="x"),
            User(id="user-b", username="b", email="b@example.test", password_hash="x"),
        ])
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_persist_order_idempotency_and_history_window(db_factory):
    store = ConversationStore()
    async with db_factory() as db:
        conversation = await store.create(db, user_id="user-a")
        first = await store.begin_turn(db, user_id="user-a", conversation_id=conversation.id,
                                       content="hello", request_id="r1")
        same = await store.begin_turn(db, user_id="user-a", conversation_id=conversation.id,
                                      content="hello", request_id="r1")
        assert same.already_started
        reply = await store.complete_turn(db, user_id="user-a", conversation_id=conversation.id,
                                          user_message_id=first.user_message.id, assistant_content="hi")
        assert reply.sequence_number == first.user_message.sequence_number + 1
        second = await store.begin_turn(db, user_id="user-a", conversation_id=conversation.id,
                                        content="again", request_id="r2")
        await store.complete_turn(db, user_id="user-a", conversation_id=conversation.id,
                                  user_message_id=second.user_message.id, assistant_content="sure")
        history = await store.list_messages(db, user_id="user-a", conversation_id=conversation.id, limit=3)
        assert [message.role for message in history] == ["assistant", "user", "assistant"]
        assert [message.sequence_number for message in history] == sorted(m.sequence_number for m in history)


@pytest.mark.asyncio
async def test_owner_validation_archive_and_interrupted_turn(db_factory):
    store = ConversationStore()
    async with db_factory() as db:
        conversation = await store.create(db, user_id="user-a")
        with pytest.raises(ConversationNotFound):
            await store.get(db, user_id="user-b", conversation_id=conversation.id)
        turn = await store.begin_turn(db, user_id="user-a", conversation_id=conversation.id, content="pending")
        await store.fail_turn(db, user_id="user-a", conversation_id=conversation.id,
                              user_message_id=turn.user_message.id)
        persisted = await db.get(ChatMessage, turn.user_message.id)
        assert persisted.turn_status == "failed"
        await store.archive(db, user_id="user-a", conversation_id=conversation.id)
        with pytest.raises(ConversationNotFound):
            await store.list_messages(db, user_id="user-a", conversation_id=conversation.id)
        assert await db.scalar(select(ChatSession.status).where(ChatSession.id == conversation.id)) == "archived"


@pytest.mark.asyncio
async def test_concurrent_turns_receive_disjoint_sequence_ranges(db_factory):
    store = ConversationStore()
    async with db_factory() as db:
        conversation = await store.create(db, user_id="user-a")
    async def begin(text):
        async with db_factory() as db:
            return await store.begin_turn(db, user_id="user-a", conversation_id=conversation.id, content=text)
    results = await asyncio.gather(begin("one"), begin("two"))
    assert len({turn.user_message.sequence_number for turn in results}) == 2
    async with db_factory() as db:
        messages = await db.scalars(select(ChatMessage).where(ChatMessage.chat_id == conversation.id))
        assert len(list(messages)) == 2
