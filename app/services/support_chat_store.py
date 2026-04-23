from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.support_chat import SupportChatMessage, SupportChatSession


class SupportChatStore:
    async def get_or_create_chat(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        chat_id: str | None,
    ) -> SupportChatSession:
        if chat_id:
            chat = await self.get_chat(db, chat_id=chat_id, tenant_id=tenant_id, user_id=user_id)
            if chat:
                return chat

        chat = SupportChatSession(
            tenant_id=tenant_id,
            user_id=user_id,
            title="Support Chat",
            status="active",
        )
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        return chat

    async def get_chat(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        tenant_id: str,
        user_id: str,
    ) -> SupportChatSession | None:
        query = select(SupportChatSession).where(
            and_(
                SupportChatSession.id == chat_id,
                SupportChatSession.tenant_id == tenant_id,
                SupportChatSession.user_id == user_id,
                SupportChatSession.status == "active",
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        tenant_id: str,
        user_id: str,
        limit: int = 30,
    ) -> list[SupportChatMessage]:
        query = (
            select(SupportChatMessage)
            .where(
                and_(
                    SupportChatMessage.chat_id == chat_id,
                    SupportChatMessage.tenant_id == tenant_id,
                    SupportChatMessage.user_id == user_id,
                )
            )
            .order_by(SupportChatMessage.created_at.asc())
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def append_messages(
        self,
        db: AsyncSession,
        *,
        chat: SupportChatSession,
        user_content: str,
        assistant_content: str,
    ) -> tuple[SupportChatMessage, SupportChatMessage]:
        user_message = SupportChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="user",
            content=user_content,
        )
        assistant_message = SupportChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="assistant",
            content=assistant_content,
        )
        db.add(user_message)
        db.add(assistant_message)

        if chat.title == "Support Chat":
            chat.title = _derive_title_from_first_message(user_content)
        chat.last_message_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(user_message)
        await db.refresh(assistant_message)
        await db.refresh(chat)
        return user_message, assistant_message


def _derive_title_from_first_message(message: str) -> str:
    clean = " ".join(message.split())
    if len(clean) <= 50:
        return clean or "Support Chat"
    return clean[:47].rstrip() + "..."
