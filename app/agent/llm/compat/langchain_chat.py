from collections.abc import AsyncIterator
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

from app.infra.llm.gemini_client import GeminiClient


class LangChainChatGateway:
    """LangChain-based chat orchestration over the configured LLM provider."""

    def __init__(self, *, gemini_client: GeminiClient) -> None:
        self._gemini_client = gemini_client
        self._prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "{system_prompt}\n\n"
                    "Retrieved context:\n{retrieval_context}\n\n"
                    "Use the context when relevant, otherwise answer normally.",
                ),
                (
                    "human",
                    "Conversation so far:\n{history_text}\n\n"
                    "User message:\n{user_message}",
                ),
            ]
        )
        self._chain = (
            self._prompt
            | RunnableLambda(self._invoke_llm_from_prompt)
            | StrOutputParser()
        )

    async def generate_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> str:
        payload = {
            "system_prompt": system_prompt,
            "user_message": user_message,
            "history_text": self._history_to_text(history),
            "retrieval_context": retrieval_context or "No additional context.",
        }
        return await self._chain.ainvoke(payload)

    async def stream_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> AsyncIterator[str]:
        prompt_text = self._render_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
            history=history,
            retrieval_context=retrieval_context,
        )
        emitted = False
        async for chunk in self._gemini_client.stream_reply(
            system_prompt="",
            user_message=prompt_text,
            history=None,
        ):
            emitted = True
            yield chunk

        if not emitted:
            full_text = await self._gemini_client.generate_reply(
                system_prompt="",
                user_message=prompt_text,
                history=None,
            )
            for chunk in _chunk_text(full_text):
                yield chunk

    async def _invoke_llm_from_prompt(self, prompt_value: Any) -> str:
        prompt_text = prompt_value.to_string()
        return await self._gemini_client.generate_reply(
            system_prompt="",
            user_message=prompt_text,
            history=None,
        )

    def _render_prompt(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> str:
        prompt_value = self._prompt.format_prompt(
            system_prompt=system_prompt,
            user_message=user_message,
            history_text=self._history_to_text(history),
            retrieval_context=retrieval_context or "No additional context.",
        )
        return prompt_value.to_string()

    def _history_to_text(self, history: list[dict[str, str]]) -> str:
        if not history:
            return "No prior messages."
        return "\n".join(
            f"{item.get('role', 'user')}: {item.get('content', '')}" for item in history[-30:]
        )


def _chunk_text(text: str, chunk_size: int = 80) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    return [clean[i : i + chunk_size] for i in range(0, len(clean), chunk_size)]
