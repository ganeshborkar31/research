"""Shared query and document embedding service."""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from app.rag.embeddings.base import (
    EmbeddingConfig,
    EmbeddingError,
    EmbeddingValidationError,
    InputEmbeddingError,
    PartialEmbeddingError,
    PermanentEmbeddingError,
    TransientEmbeddingError,
)
from app.rag.models.chunk import DocumentChunk
from app.rag.ports.embedding_provider import EmbeddingProvider


@dataclass(frozen=True, slots=True)
class EmbeddedChunk:
    chunk: DocumentChunk
    vector: list[float]
    provider: str
    model: str | None
    dimensions: int


class EmbeddingService:
    """Generate and validate vectors using one provider/config for queries and documents."""

    def __init__(self, provider: EmbeddingProvider, config: EmbeddingConfig) -> None:
        self.provider = provider
        self.config = config
        self.dimensions = config.dimensions or getattr(provider, "dimensions", None)
        if not isinstance(self.dimensions, int) or self.dimensions <= 0:
            raise ValueError("embedding dimensions must be configured by the service or provider")
        provider_dimensions = getattr(provider, "dimensions", self.dimensions)
        if provider_dimensions != self.dimensions:
            raise ValueError(
                f"Configured embedding dimension {self.dimensions} does not match provider dimension {provider_dimensions}"
            )

    async def embed_query(self, text: str) -> list[float]:
        self._validate_texts([text])
        try:
            result = await self._with_retry(lambda: self.provider.embed_query(text))
        except EmbeddingError:
            raise
        except Exception as exc:
            raise PermanentEmbeddingError(f"Query embedding failed: {self._safe_message(exc)}") from exc
        return self._validate_vector(result, input_index=0)

    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in configured batches; a partial failure returns no vectors."""
        self._validate_texts(texts)
        all_vectors: list[list[float]] = []
        failed_indices: list[int] = []
        for offset in range(0, len(texts), self.config.batch_size):
            batch = list(texts[offset:offset + self.config.batch_size])
            vectors, failures = await self._embed_batch_resilient(batch)
            if failures:
                failed_indices.extend(offset + index for index in failures)
            all_vectors.extend(vectors)
        if failed_indices:
            raise PartialEmbeddingError(tuple(failed_indices))
        return all_vectors

    async def embed_documents(self, chunks: Sequence[DocumentChunk]) -> list[EmbeddedChunk]:
        if any(not isinstance(chunk, DocumentChunk) for chunk in chunks):
            raise ValueError("document embedding requires DocumentChunk values")
        vectors = await self.embed_texts([chunk.content for chunk in chunks])
        return [
            EmbeddedChunk(
                chunk=chunk,
                vector=vector,
                provider=self.config.provider,
                model=self.config.model or getattr(self.provider, "model", None),
                dimensions=self.dimensions,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    async def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            await close()

    async def _embed_batch_resilient(self, texts: list[str]) -> tuple[list[list[float]], list[int]]:
        try:
            response = await self._with_retry(lambda: self.provider.embed_documents(texts))
            vectors = list(response)
            if len(vectors) != len(texts):
                return [], list(range(len(texts)))
            checked: list[list[float]] = []
            failures: list[int] = []
            for index, vector in enumerate(vectors):
                try:
                    checked.append(self._validate_vector(vector, input_index=index))
                except EmbeddingValidationError:
                    checked.append([])
                    failures.append(index)
            return checked, failures
        except InputEmbeddingError:
            if len(texts) == 1:
                return [[]], [0]
            midpoint = len(texts) // 2
            left, left_failures = await self._embed_batch_resilient(texts[:midpoint])
            right, right_failures = await self._embed_batch_resilient(texts[midpoint:])
            return left + right, left_failures + [midpoint + index for index in right_failures]
        except PartialEmbeddingError:
            raise
        except EmbeddingError:
            raise
        except Exception as exc:
            raise PermanentEmbeddingError(f"Document embedding failed: {self._safe_message(exc)}") from exc

    async def _with_retry(self, request: Any) -> Any:
        for attempt in range(self.config.max_retries + 1):
            try:
                return await asyncio.wait_for(request(), timeout=self.config.timeout_seconds)
            except InputEmbeddingError:
                raise
            except PermanentEmbeddingError:
                raise
            except Exception as exc:
                if not self._is_transient(exc):
                    if isinstance(exc, PermanentEmbeddingError):
                        raise
                    raise PermanentEmbeddingError(self._safe_message(exc)) from exc
                if attempt >= self.config.max_retries:
                    raise TransientEmbeddingError(
                        f"Embedding provider remained unavailable after {attempt + 1} attempts"
                    ) from exc
                delay = self.config.retry_backoff_seconds * (2 ** attempt)
                if delay:
                    await asyncio.sleep(delay + random.uniform(0, delay * 0.1))
        raise AssertionError("unreachable")

    def _validate_vector(self, vector: Any, *, input_index: int) -> list[float]:
        if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
            raise EmbeddingValidationError(f"Embedding {input_index} is not a numeric vector")
        if len(vector) != self.dimensions:
            raise EmbeddingValidationError(
                f"Embedding {input_index} has dimension {len(vector)}; expected {self.dimensions}"
            )
        output: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise EmbeddingValidationError(f"Embedding {input_index} contains a non-finite or non-numeric value")
            output.append(float(value))
        return output

    @staticmethod
    def _validate_texts(texts: Sequence[str]) -> None:
        if not isinstance(texts, Sequence) or isinstance(texts, (str, bytes)):
            raise ValueError("texts must be a sequence of strings")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        if isinstance(exc, (TransientEmbeddingError, TimeoutError, asyncio.TimeoutError, ConnectionError)):
            return True
        status = EmbeddingService._status_code(exc)
        try:
            if int(status) in {408, 425, 429, 500, 502, 503, 504}:
                return True
        except (TypeError, ValueError):
            pass
        name = type(exc).__name__.lower()
        return any(marker in name for marker in ("timeout", "connection", "ratelimit", "serviceunavailable", "internalserver"))

    @staticmethod
    def _status_code(exc: Exception) -> int | None:
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status is None:
            status = getattr(getattr(exc, "response", None), "status_code", None)
        try:
            return int(status)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_message(exc: Exception) -> str:
        # Provider exception text may contain request details; report only its type.
        if isinstance(exc, EmbeddingError):
            return str(exc)
        return f"{type(exc).__name__}: embedding request was rejected"
