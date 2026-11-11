"""Conversation persistence for the production agent.

Identity is always supplied by the authenticated caller. This store never accepts
an owner identifier from an agent request body.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession


class ConversationNotFound(LookupError):
    """The conversation does not exist, is archived, or belongs to another user."""


@dataclass(frozen=True)
class ConversationTurn:
    user_message: ChatMessage
    already_started: bool = False


class ConversationStore:
    """User-scoped access to the existing chat session and message tables."""

    async def create(self, db: AsyncSession, *, user_id: str, title: str = "New Chat") -> ChatSession:
        conversation = ChatSession(user_id=user_id, title=title.strip()[:255] or "New Chat")
        db.add(conversation)
        await db.commit()
        await db.refresh(conversation)
        return conversation

    async def get(self, db: AsyncSession, *, user_id: str, conversation_id: str) -> ChatSession:
        result = await db.execute(select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == user_id,
            ChatSession.status == "active",
        ))
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFound(conversation_id)
        return conversation

    async def list_messages(
        self, db: AsyncSession, *, user_id: str, conversation_id: str,
        limit: int = 100, before_sequence: int | None = None,
    ) -> list[ChatMessage]:
        if limit < 1:
            raise ValueError("limit must be positive")
        await self.get(db, user_id=user_id, conversation_id=conversation_id)
        query = select(ChatMessage).where(ChatMessage.chat_id == conversation_id)
        if before_sequence is not None:
            query = query.where(ChatMessage.sequence_number < before_sequence)
        # Fetch the newest bounded window, then return it chronologically.
        result = await db.execute(query.order_by(ChatMessage.sequence_number.desc()).limit(limit))
        return list(reversed(result.scalars().all()))

    async def begin_turn(
        self, db: AsyncSession, *, user_id: str, conversation_id: str,
        content: str, request_id: str | None = None,
    ) -> ConversationTurn:
        text = content.strip()
        if not text:
            raise ValueError("Message cannot be empty")
        if request_id is not None and len(request_id) > 128:
            raise ValueError("request_id must be 128 characters or fewer")
        # Atomic counter update serializes allocation on PostgreSQL and SQLite.
        allocation = await db.execute(update(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == user_id,
            ChatSession.status == "active",
        ).values(next_message_sequence=ChatSession.next_message_sequence + 2)
         .returning(ChatSession.next_message_sequence))
        next_sequence = allocation.scalar_one_or_none()
        if next_sequence is None:
            raise ConversationNotFound(conversation_id)
        conversation_result = await db.execute(select(ChatSession).where(ChatSession.id == conversation_id)
                                               .execution_options(populate_existing=True))
        conversation = conversation_result.scalar_one()
        request_id = request_id or str(uuid4())
        if request_id:
            prior = await db.execute(select(ChatMessage).where(
                ChatMessage.chat_id == conversation_id,
                ChatMessage.request_id == request_id,
                ChatMessage.role == "user",
            ))
            existing = prior.scalar_one_or_none()
            if existing is not None:
                conversation.next_message_sequence = next_sequence - 2
                if existing.content != text:
                    await db.commit()
                    raise ValueError("request_id was already used for a different message")
                await db.commit()
                return ConversationTurn(existing, already_started=True)

        sequence = next_sequence - 2
        user_message = ChatMessage(
            chat_id=conversation.id, user_id=user_id, role="user", content=text,
            sequence_number=sequence, request_id=request_id, turn_status="pending",
        )
        conversation.last_message_at = datetime.now(timezone.utc)
        if conversation.title == "New Chat":
            conversation.title = " ".join(text.split())[:50] or "New Chat"
        db.add(user_message)
        await db.commit()
        await db.refresh(user_message)
        return ConversationTurn(user_message)

    async def complete_turn(
        self, db: AsyncSession, *, user_id: str, conversation_id: str,
        user_message_id: str, assistant_content: str,
    ) -> ChatMessage:
        result = await db.execute(select(ChatMessage).where(
            ChatMessage.id == user_message_id,
            ChatMessage.chat_id == conversation_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
        ).with_for_update())
        user_message = result.scalar_one_or_none()
        conversation_result = await db.execute(select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == user_id,
            ChatSession.status == "active",
        ))
        if user_message is None or conversation_result.scalar_one_or_none() is None:
            raise ConversationNotFound(conversation_id)
        if user_message.turn_status == "completed":
            existing = await db.execute(select(ChatMessage).where(
                ChatMessage.chat_id == conversation_id,
                ChatMessage.request_id == user_message.request_id,
                ChatMessage.role == "assistant",
            ))
            assistant = existing.scalar_one_or_none()
            if assistant is not None:
                await db.rollback()
                return assistant
        assistant = ChatMessage(
            chat_id=conversation_id, user_id=user_id, role="assistant",
            content=assistant_content, sequence_number=user_message.sequence_number + 1,
            request_id=user_message.request_id, turn_status="completed",
        )
        user_message.turn_status = "completed"
        db.add(assistant)
        await db.commit()
        await db.refresh(assistant)
        return assistant

    async def fail_turn(
        self, db: AsyncSession, *, user_id: str, conversation_id: str, user_message_id: str,
    ) -> None:
        result = await db.execute(select(ChatMessage).where(
            ChatMessage.id == user_message_id,
            ChatMessage.chat_id == conversation_id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
            ChatMessage.turn_status == "pending",
        ).with_for_update())
        message = result.scalar_one_or_none()
        if message is not None:
            message.turn_status = "failed"
            await db.commit()
        else:
            await db.rollback()

    async def archive(self, db: AsyncSession, *, user_id: str, conversation_id: str) -> None:
        conversation = await self.get(db, user_id=user_id, conversation_id=conversation_id)
        conversation.status = "archived"
        await db.commit()

