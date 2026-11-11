"""Provider-independent assembly and budgeting of conversation context."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextMessage:
    role: str
    content: str
    name: str | None = None


class ContextLimitError(ValueError):
    pass


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free fallback; callers can inject a model tokenizer."""
    return max(1, (len(text) + 3) // 4) if text else 0


class ContextManager:
    def __init__(
        self,
        *,
        max_tokens: int = 12_000,
        max_history_messages: int = 30,
        token_counter: Callable[[str], int] = estimate_tokens,
    ) -> None:
        if max_tokens < 1 or max_history_messages < 0:
            raise ValueError("context limits must be positive")
        self.max_tokens = max_tokens
        self.max_history_messages = max_history_messages
        self.token_counter = token_counter

    def prepare(
        self,
        *,
        system_prompt: str,
        current_user_message: str,
        history: Sequence[ContextMessage | object] = (),
        retrieved_context: str | None = None,
        tool_results: Sequence[ContextMessage] = (),
        max_tokens: int | None = None,
    ) -> list[ContextMessage]:
        budget = max_tokens if max_tokens is not None else self.max_tokens
        if budget < 1:
            raise ValueError("max_tokens must be positive")
        fixed = [ContextMessage("system", system_prompt)]
        if retrieved_context:
            fixed.append(ContextMessage("system", f"Retrieved context:\n{retrieved_context}"))
        current = ContextMessage("user", current_user_message)
        tools = list(tool_results)
        fixed_cost = sum(self.token_counter(message.content) for message in fixed + [current, *tools])
        if fixed_cost > budget:
            raise ContextLimitError("System prompt, current message, retrieval, and tool results exceed context limit")

        normalized = [self._normalize(message) for message in history]
        # Keep the newest history within its configured count and token budget,
        # while retaining the original chronological order in the result.
        selected: list[ContextMessage] = []
        remaining = budget - fixed_cost
        for message in reversed(normalized[-self.max_history_messages:] if self.max_history_messages else []):
            cost = self.token_counter(message.content)
            if cost > remaining:
                break
            selected.append(message)
            remaining -= cost
        selected.reverse()
        return [*fixed, *selected, current, *tools]

    @staticmethod
    def _normalize(message: object) -> ContextMessage:
        if isinstance(message, ContextMessage):
            return message
        if isinstance(message, dict):
            role, content = message.get("role"), message.get("content")
            name = message.get("name")
        else:
            role, content = getattr(message, "role", None), getattr(message, "content", None)
            name = getattr(message, "name", None)
        if role not in {"user", "assistant", "system", "tool"} or not isinstance(content, str):
            raise ValueError("history messages must have a supported role and string content")
        return ContextMessage(role, content, name)

