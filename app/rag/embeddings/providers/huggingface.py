"""Hugging Face Inference API embedding provider adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.rag.embeddings.base import PermanentEmbeddingError


class HuggingFaceEmbeddingProvider:
    def __init__(self, *, model: str, dimensions: int, token: str | None = None,
                 client: Any | None = None) -> None:
        self.model = model
        self.dimensions = dimensions
        if client is None:
            try:
                from huggingface_hub import AsyncInferenceClient
            except ImportError as exc:
                raise PermanentEmbeddingError("huggingface-hub is required for Hugging Face embeddings") from exc
            client = AsyncInferenceClient(token=token)
        self._client = client

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self._client.feature_extraction(
            text=list(texts), model=self.model
        )
        values = response.tolist() if hasattr(response, "tolist") else response
        if values and isinstance(values[0], (int, float)):
            values = [values]
        return [list(vector) for vector in values]

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result
