from collections.abc import AsyncIterator
from typing import Protocol


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
