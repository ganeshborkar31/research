"""Provider-neutral chat generation contracts and normalized values."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"Unsupported chat role: {self.role}")


@dataclass(frozen=True, slots=True)
class GenerationOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    stop: Sequence[str] | None = None
    response_format: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatModelConfig:
    """Provider selection and transport settings owned by the agent layer."""

    provider: str = "gemini"
    model: str | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not self.provider.strip() or self.timeout_seconds <= 0 or self.max_retries < 0 or self.retry_backoff_seconds < 0:
            raise ValueError("chat model provider configuration is invalid")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: Any = None


@dataclass(frozen=True, slots=True)
class ChatChunk:
    content: str
    usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class ChatCapabilities:
    streaming: bool = True
    structured_output: bool = False
    usage_in_stream: bool = False


class ChatModelError(RuntimeError):
    """Base error safe to expose at the provider boundary."""


class ChatProviderSelectionError(ChatModelError):
    pass


class ChatProviderError(ChatModelError):
    pass


class ChatModel(Protocol):
    model: str
    capabilities: ChatCapabilities

    async def generate(
        self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None
    ) -> ChatResponse: ...

    def stream(
        self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None
    ) -> AsyncIterator[ChatChunk]: ...

    async def generate_structured(
        self, messages: Sequence[ChatMessage], schema: Any, *, options: GenerationOptions | None = None
    ) -> Any: ...


def normalize_usage(usage: Any) -> TokenUsage:
    """Accept common SDK/LangChain usage shapes without leaking SDK types."""
    if not usage:
        return TokenUsage()
    if not isinstance(usage, Mapping):
        usage = vars(usage) if hasattr(usage, "__dict__") else {}
    prompt = usage.get("input_tokens", usage.get("prompt_tokens"))
    completion = usage.get("output_tokens", usage.get("completion_tokens"))
    total = usage.get("total_tokens")
    return TokenUsage(
        input_tokens=int(prompt) if prompt is not None else None,
        output_tokens=int(completion) if completion is not None else None,
        total_tokens=int(total) if total is not None else (
            int(prompt) + int(completion) if prompt is not None and completion is not None else None
        ),
    )
