import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infra.llm import GeminiClient, LangChainChatGateway, LangGraphChatGateway
from app.models.chat import ChatMessage, ChatSession
from app.schemas.user_memory import UserHistoryEventIn
from app.services.ports import ChatRuntimePort, UserMemoryPort
from app.services.user_vector_store import UserVectorStore


SYSTEM_PROMPT = (
    "You are a helpful, accurate, and concise AI assistant. "
    "Answer clearly and keep context from the conversation."
)
logger = logging.getLogger(__name__)


class ChatService:
    def __init__(
        self,
        *,
        runtime: ChatRuntimePort | None = None,
        memory_store: UserMemoryPort | None = None,
    ) -> None:
        if runtime is None:
            settings = get_settings()
            gemini_client = GeminiClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
            )
            if settings.use_langgraph_chat and LangGraphChatGateway:
                runtime = LangGraphChatGateway(gemini_client=gemini_client)
            else:
                if settings.use_langgraph_chat and not LangGraphChatGateway:
                    logger.warning("use_langgraph_chat=true, but langgraph is unavailable; using LangChain.")
                runtime = LangChainChatGateway(gemini_client=gemini_client)

        self._runtime = runtime
        self._vector_store = memory_store or UserVectorStore()

    async def create_chat(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        title: str | None = None,
    ) -> ChatSession:
        chat = ChatSession(
            tenant_id=tenant_id,
            user_id=user_id,
            title=(title or "New Chat").strip()[:255] or "New Chat",
            status="active",
        )
        db.add(chat)
        await db.commit()
        await db.refresh(chat)
        return chat

    async def list_chats(
        self,
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ChatSession]:
        query = (
            select(ChatSession)
            .where(
                and_(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.user_id == user_id,
                    ChatSession.status == "active",
                )
            )
            .order_by(ChatSession.last_message_at.desc().nullslast(), ChatSession.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_chat(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        tenant_id: str,
        user_id: str,
    ) -> ChatSession | None:
        query = select(ChatSession).where(
            and_(
                ChatSession.id == chat_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.user_id == user_id,
                ChatSession.status == "active",
            )
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def update_chat_title(
        self,
        db: AsyncSession,
        *,
        chat: ChatSession,
        title: str,
    ) -> ChatSession:
        chat.title = title.strip()[:255] or chat.title
        await db.commit()
        await db.refresh(chat)
        return chat

    async def delete_chat(self, db: AsyncSession, *, chat: ChatSession) -> None:
        chat.status = "archived"
        await db.commit()

    async def list_messages(
        self,
        db: AsyncSession,
        *,
        chat_id: str,
        tenant_id: str,
        user_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ChatMessage]:
        query = (
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.chat_id == chat_id,
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.user_id == user_id,
                )
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def send_message(
        self,
        db: AsyncSession,
        *,
        chat: ChatSession,
        message: str,
    ) -> tuple[ChatMessage, ChatMessage]:
        user_content = message.strip()
        if not user_content:
            raise ValueError("Message cannot be empty.")

        history_query = (
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.chat_id == chat.id,
                    ChatMessage.tenant_id == chat.tenant_id,
                    ChatMessage.user_id == chat.user_id,
                )
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(30)
        )
        history_result = await db.execute(history_query)
        history_messages = list(history_result.scalars().all())
        history = [{"role": m.role, "content": m.content} for m in history_messages]

        retrieval_context = await self._build_retrieval_context(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            chat_id=chat.id,
            query=user_content,
        )
        assistant_reply = await self._runtime.generate_reply(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_content,
            history=history,
            retrieval_context=retrieval_context,
        )

        user_message = ChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="user",
            content=user_content,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="assistant",
            content=assistant_reply,
        )
        db.add(user_message)
        db.add(assistant_message)

        if chat.title == "New Chat":
            chat.title = _derive_title_from_first_message(user_content)
        chat.last_message_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(user_message)
        await db.refresh(assistant_message)
        await db.refresh(chat)
        await self._store_history_event(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            session_id=chat.id,
            message=user_content,
            role="user",
        )
        await self._store_history_event(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            session_id=chat.id,
            message=assistant_reply,
            role="assistant",
        )
        return user_message, assistant_message

    async def stream_message(
        self,
        db: AsyncSession,
        *,
        chat: ChatSession,
        message: str,
    ) -> AsyncIterator[str]:
        user_content = message.strip()
        if not user_content:
            raise ValueError("Message cannot be empty.")

        history_query = (
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.chat_id == chat.id,
                    ChatMessage.tenant_id == chat.tenant_id,
                    ChatMessage.user_id == chat.user_id,
                )
            )
            .order_by(ChatMessage.created_at.asc())
            .limit(30)
        )
        history_result = await db.execute(history_query)
        history_messages = list(history_result.scalars().all())
        history = [{"role": m.role, "content": m.content} for m in history_messages]
        retrieval_context = await self._build_retrieval_context(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            chat_id=chat.id,
            query=user_content,
        )

        chunks: list[str] = []
        async for chunk in self._runtime.stream_reply(
            system_prompt=SYSTEM_PROMPT,
            user_message=user_content,
            history=history,
            retrieval_context=retrieval_context,
        ):
            chunks.append(chunk)
            yield chunk

        assistant_reply = "".join(chunks).strip()
        user_message = ChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="user",
            content=user_content,
        )
        assistant_message = ChatMessage(
            chat_id=chat.id,
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            role="assistant",
            content=assistant_reply,
        )
        db.add(user_message)
        db.add(assistant_message)

        if chat.title == "New Chat":
            chat.title = _derive_title_from_first_message(user_content)
        chat.last_message_at = datetime.now(timezone.utc)

        await db.commit()
        await self._store_history_event(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            session_id=chat.id,
            message=user_content,
            role="user",
        )
        await self._store_history_event(
            tenant_id=chat.tenant_id,
            user_id=chat.user_id,
            session_id=chat.id,
            message=assistant_reply,
            role="assistant",
        )

    async def _build_retrieval_context(
        self,
        *,
        tenant_id: str,
        user_id: str,
        chat_id: str,
        query: str,
    ) -> str:
        try:
            doc_hits = await self._vector_store.search_user_documents(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                chat_id=chat_id,
                include_global=True,
                limit=3,
            )
            history_hits = await self._vector_store.search_user_history(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                chat_id=chat_id,
                limit=3,
            )
        except Exception as exc:
            logger.warning(
                "Qdrant retrieval unavailable for tenant_id=%s user_id=%s: %s",
                tenant_id,
                user_id,
                exc,
            )
            return "No retrieval context available."

        snippets: list[str] = []
        for hit in doc_hits:
            snippets.append(f"[doc] {hit.content}")
        for hit in history_hits:
            snippets.append(f"[history] {hit.content}")

        if not snippets:
            return "No retrieval context available."
        return "\n".join(snippets)

    async def _store_history_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        message: str,
        role: str,
    ) -> None:
        try:
            await self._vector_store.store_history_event(
                UserHistoryEventIn(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    message=message,
                    role=role,
                    metadata={},
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to index chat history in Qdrant for tenant_id=%s user_id=%s session_id=%s: %s",
                tenant_id,
                user_id,
                session_id,
                exc,
            )
            return


def _derive_title_from_first_message(message: str) -> str:
    clean = " ".join(message.split())
    if len(clean) <= 50:
        return clean or "New Chat"
    return clean[:47].rstrip() + "..."


_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service
