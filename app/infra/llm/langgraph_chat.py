from collections.abc import AsyncIterator
from typing import TypedDict

from langchain_core.prompts import ChatPromptTemplate

from app.infra.llm.gemini_client import GeminiClient


class _ChatState(TypedDict, total=False):
    system_prompt: str
    user_message: str
    history: list[dict[str, str]]
    retrieval_context: str
    prompt_text: str
    reply: str


class LangGraphChatGateway:
    """LangGraph orchestration for retrieval-aware chat generation."""

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

        # Keep import local so the project can run even if langgraph is optional.
        from langgraph.graph import END, StateGraph

        builder = StateGraph(_ChatState)
        builder.add_node("render_prompt", self._render_prompt_node)
        builder.add_node("generate_reply", self._generate_reply_node)
        builder.set_entry_point("render_prompt")
        builder.add_edge("render_prompt", "generate_reply")
        builder.add_edge("generate_reply", END)
        self._graph = builder.compile()

    async def generate_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> str:
        final_state = await self._graph.ainvoke(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "history": history,
                "retrieval_context": retrieval_context,
            }
        )
        return str(final_state.get("reply", "")).strip()

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

    def _render_prompt_node(self, state: _ChatState) -> _ChatState:
        prompt_text = self._render_prompt(
            system_prompt=state.get("system_prompt", ""),
            user_message=state.get("user_message", ""),
            history=state.get("history", []),
            retrieval_context=state.get("retrieval_context", ""),
        )
        return {"prompt_text": prompt_text}

    async def _generate_reply_node(self, state: _ChatState) -> _ChatState:
        prompt_text = state.get("prompt_text", "")
        reply = await self._gemini_client.generate_reply(
            system_prompt="",
            user_message=prompt_text,
            history=None,
        )
        return {"reply": reply}

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
