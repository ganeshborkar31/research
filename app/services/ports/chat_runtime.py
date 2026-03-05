from collections.abc import AsyncIterator
from typing import Protocol

from app.schemas.user_memory import UserHistoryEventIn, UserMemoryHit


class ChatRuntimePort(Protocol):
    async def generate_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> str: ...

    async def stream_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> AsyncIterator[str]: ...


class UserMemoryPort(Protocol):
    async def search_user_documents(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        include_global: bool = True,
        limit: int = 5,
    ) -> list[UserMemoryHit]: ...

    async def search_user_history(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        limit: int = 5,
    ) -> list[UserMemoryHit]: ...

    async def store_history_event(self, payload: UserHistoryEventIn) -> None: ...
