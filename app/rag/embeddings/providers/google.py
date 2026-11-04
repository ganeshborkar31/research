"""Google Gen AI embeddings provider adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.rag.embeddings.base import PermanentEmbeddingError


class GoogleEmbeddingProvider:
    def __init__(self, *, model: str, dimensions: int, api_key: str,
                 client: Any | None = None, types_module: Any | None = None) -> None:
        if not api_key:
            raise PermanentEmbeddingError("GEMINI_API_KEY is required for the configured embedding provider")
        self.model = model
        self.dimensions = dimensions
        if client is None or types_module is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise PermanentEmbeddingError("google-genai is required for Google embeddings") from exc
            if client is None:
                client = genai.Client(api_key=api_key).aio
            types_module = types
        self._client = client
        self._types = types_module

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "RETRIEVAL_QUERY"))[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT")

    async def _embed(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        response = await self._client.models.embed_content(
            model=self.model,
            contents=list(texts),
            config=self._types.EmbedContentConfig(
                output_dimensionality=self.dimensions,
                task_type=task_type,
            ),
        )
        return [list(item.values) for item in (response.embeddings or [])]

    async def close(self) -> None:
        aclose = getattr(self._client, "aclose", None)
        if callable(aclose):
            await aclose()
