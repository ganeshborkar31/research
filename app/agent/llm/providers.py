"""Chat model adapters. Provider SDK details stay inside this module."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

from app.core.config import Settings, get_settings
from app.agent.llm.chat_model import (
    ChatCapabilities, ChatChunk, ChatMessage, ChatProviderError,
    ChatModelConfig, ChatProviderSelectionError, ChatResponse, GenerationOptions,
    TokenUsage, normalize_usage,
)


class GeminiChatModel:
    """Adapter over the repository's existing Gemini REST integration."""

    capabilities = ChatCapabilities(streaming=True, structured_output=False)

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    @staticmethod
    def _split(messages: Sequence[ChatMessage]) -> tuple[str, str, list[dict[str, str]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        conversation = [m for m in messages if m.role != "system"]
        if not conversation:
            raise ValueError("at least one user or assistant message is required")
        latest = conversation[-1]
        history = [{"role": m.role, "content": m.content} for m in conversation[:-1]]
        return system, latest.content, history

    async def generate(self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None) -> ChatResponse:
        system, user, history = self._split(messages)
        try:
            text = await self.client.generate_reply(system_prompt=system, user_message=user, history=history)
            return ChatResponse(content=text, model=self.model)
        except Exception as exc:
            raise ChatProviderError("Gemini generation failed") from exc

    async def stream(self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None) -> AsyncIterator[ChatChunk]:
        system, user, history = self._split(messages)
        try:
            async for chunk in self.client.stream_reply(system_prompt=system, user_message=user, history=history):
                yield ChatChunk(content=chunk)
        except Exception as exc:
            raise ChatProviderError("Gemini streaming failed") from exc

    async def generate_structured(self, messages: Sequence[ChatMessage], schema: Any, *, options: GenerationOptions | None = None) -> Any:
        raise NotImplementedError("Gemini structured output is not exposed by the configured adapter")


class OpenAIChatModel:
    capabilities = ChatCapabilities(streaming=True, structured_output=True)

    def __init__(self, *, model: str, api_key: str, base_url: str, timeout: float,
                 max_retries: int, retry_backoff_seconds: float) -> None:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ChatProviderSelectionError("langchain-openai is required for the OpenAI chat provider") from exc
        self.model = model
        self._llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                               timeout=timeout, max_retries=0, streaming=True)
        self._retries = max_retries
        self._backoff = retry_backoff_seconds

    @staticmethod
    def _messages(messages: Sequence[ChatMessage]) -> list[tuple[str, str]]:
        return [(m.role, m.content) for m in messages]

    def _params(self, options: GenerationOptions | None) -> dict[str, Any]:
        if options is None:
            return {}
        result: dict[str, Any] = {}
        if options.temperature is not None: result["temperature"] = options.temperature
        if options.max_tokens is not None: result["max_tokens"] = options.max_tokens
        if options.stop is not None: result["stop"] = list(options.stop)
        return result

    async def _retry(self, operation):
        for attempt in range(self._retries + 1):
            try:
                return await operation()
            except Exception as exc:
                if attempt >= self._retries or not _is_transient(exc):
                    raise ChatProviderError("OpenAI chat request failed") from exc
                await asyncio.sleep(self._backoff * (2 ** attempt) + random.random() * self._backoff)

    async def generate(self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None) -> ChatResponse:
        async def call():
            return await self._llm.ainvoke(self._messages(messages), **self._params(options))
        try:
            result = await self._retry(call)
            metadata = getattr(result, "response_metadata", {}) or {}
            usage = metadata.get("token_usage") or metadata.get("usage") or getattr(result, "usage_metadata", {})
            return ChatResponse(content=str(result.content), model=metadata.get("model_name", self.model),
                                usage=normalize_usage(usage), raw=result)
        except ChatProviderError:
            raise
        except Exception as exc:
            raise ChatProviderError("OpenAI chat request failed") from exc

    async def stream(self, messages: Sequence[ChatMessage], *, options: GenerationOptions | None = None) -> AsyncIterator[ChatChunk]:
        # Retry only before first output; retrying after emitted chunks duplicates content.
        emitted = False
        try:
            async for chunk in self._llm.astream(self._messages(messages), **self._params(options)):
                content = chunk.content if isinstance(chunk.content, str) else ""
                if content:
                    emitted = True
                    yield ChatChunk(content=content)
        except Exception as exc:
            if not emitted and _is_transient(exc) and self._retries:
                await asyncio.sleep(self._backoff)
                async for chunk in self._llm.astream(self._messages(messages), **self._params(options)):
                    content = chunk.content if isinstance(chunk.content, str) else ""
                    if content:
                        emitted = True
                        yield ChatChunk(content=content)
                return
            raise ChatProviderError("OpenAI streaming failed") from exc

    async def generate_structured(self, messages: Sequence[ChatMessage], schema: Any, *, options: GenerationOptions | None = None) -> Any:
        try:
            structured = self._llm.with_structured_output(schema)
            return await structured.ainvoke(self._messages(messages), **self._params(options))
        except Exception as exc:
            raise ChatProviderError("OpenAI structured generation failed") from exc


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    return any(term in name for term in ("timeout", "connection", "ratelimit", "serviceunavailable", "internalserver")) or getattr(exc, "status_code", 0) in {408, 429, 500, 502, 503, 504}


def build_chat_model(
    settings: Settings | None = None,
    *,
    config: ChatModelConfig | None = None,
    provider_builders: Mapping[str, Callable[[Settings, ChatModelConfig], Any]] | None = None,
):
    settings = settings or get_settings()
    config = config or ChatModelConfig(
        provider=getattr(settings, "llm_provider", "gemini"),
        model=getattr(settings, "llm_model", None),
        timeout_seconds=getattr(settings, "llm_timeout_seconds", 30.0),
        max_retries=getattr(settings, "llm_max_retries", 2),
        retry_backoff_seconds=getattr(settings, "llm_retry_backoff_seconds", 0.25),
    )
    provider = config.provider.strip().lower()
    model = config.model or (settings.gemini_model if provider == "gemini" else "gpt-4o-mini")
    timeout, retries, backoff = config.timeout_seconds, config.max_retries, config.retry_backoff_seconds
    if timeout <= 0 or retries < 0:
        raise ChatProviderSelectionError("LLM timeout and retry configuration is invalid")
    custom_builder = (provider_builders or {}).get(provider)
    if custom_builder is not None:
        return custom_builder(settings, config)
    if provider == "gemini":
        # Reuse the existing provider transport only. Prompt construction,
        # persistence, retrieval, and orchestration remain in app.agent.
        from app.agent.llm.gemini_client import GeminiClient

        return GeminiChatModel(
            client=GeminiClient(
                api_key=settings.gemini_api_key,
                model=model,
                timeout_seconds=int(timeout),
            ),
            model=model,
        )
    if provider == "openai":
        if not settings.openai_api_key:
            raise ChatProviderSelectionError("OPENAI_API_KEY is required for the OpenAI chat provider")
        return OpenAIChatModel(model=model, api_key=settings.openai_api_key,
                               base_url=settings.openai_base_url, timeout=timeout,
                               max_retries=retries, retry_backoff_seconds=backoff)
    raise ChatProviderSelectionError(f"Unsupported chat provider: {provider}")
