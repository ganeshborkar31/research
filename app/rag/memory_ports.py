from typing import Protocol

from app.schemas.user_memory import UserHistoryEventIn, UserMemoryHit


class UserMemoryPort(Protocol):
    async def search_user_documents(
        self,
        *,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        include_global: bool = True,
        limit: int = 5,
    ) -> list[UserMemoryHit]: ...

    async def search_user_history(
        self,
        *,
        user_id: str,
        query: str,
        chat_id: str | None = None,
        limit: int = 5,
    ) -> list[UserMemoryHit]: ...

    async def store_history_event(self, payload: UserHistoryEventIn) -> None: ...
