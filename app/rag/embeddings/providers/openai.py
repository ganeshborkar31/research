"""OpenAI embeddings provider adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.rag.embeddings.base import PermanentEmbeddingError


class OpenAIEmbeddingProvider:
    def __init__(self, *, model: str, dimensions: int, api_key: str, base_url: str,
                 timeout: float = 30.0, client: Any | None = None) -> None:
        if not api_key:
            raise PermanentEmbeddingError("OPENAI_API_KEY is required for the configured embedding provider")
        self.model = model
        self.dimensions = dimensions
        if client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise PermanentEmbeddingError("The OpenAI SDK is required for OpenAI embeddings") from exc
            client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._client = client

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        return list(vectors[0])

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        options: dict[str, Any] = {"model": self.model, "input": list(texts)}
        if self.model.startswith("text-embedding-3"):
            options["dimensions"] = self.dimensions
        response = await self._client.embeddings.create(**options)
        entries = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in entries]

    async def close(self) -> None:
        await self._client.close()
