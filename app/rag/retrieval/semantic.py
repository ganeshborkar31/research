"""Dense retrieval using the shared embedding service and existing Qdrant adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.rag.embeddings.service import EmbeddingService
from app.rag.models.retrieval import (
    RetrievalQueryContext,
    RetrievalResult,
    RetrievalStrategyName,
)
from app.rag.ports.vector_store import DenseSearchPort
from app.infra.vector_stores.qdrant import EmptyRetrievalScope, QdrantVectorStore, VectorStoreError


class DenseRetrievalError(RuntimeError):
    """A dense retrieval operation failed or returned incompatible data."""


class DenseRetrievalConfigurationError(DenseRetrievalError):
    """The query embedder and existing Qdrant collection are incompatible."""


class DenseRetriever:
    """Embed a query and retrieve only its authorized active document versions."""

    def __init__(self, *, embeddings: EmbeddingService, vector_store: DenseSearchPort) -> None:
        self.embeddings = embeddings
        self.vector_store = vector_store
        if embeddings.dimensions != vector_store.vector_size:
            raise DenseRetrievalConfigurationError(
                "Embedding and configured Qdrant dimensions do not match"
            )

    async def retrieve(self, context: RetrievalQueryContext) -> list[RetrievalResult]:
        if context.strategy != RetrievalStrategyName.DENSE:
            raise ValueError("DenseRetriever requires the dense retrieval strategy")
        if not context.active_version_ids:
            return []

        try:
            await self.vector_store.validate_vector_dimensions(self.embeddings.dimensions)
            vector = await self.embeddings.embed_query(context.query)
            if len(vector) != self.vector_store.vector_size:
                raise DenseRetrievalConfigurationError(
                    "Query embedding dimension does not match the existing Qdrant vector"
                )
            point_filter = QdrantVectorStore.build_retrieval_filter(context)
            hits = await self.vector_store.search_filtered(
                vector, limit=context.top_k, query_filter=point_filter
            )
        except EmptyRetrievalScope:
            return []
        except DenseRetrievalError:
            raise
        except Exception as exc:
            raise DenseRetrievalError("Dense retrieval failed") from exc

        results: list[RetrievalResult] = []
        for hit in hits:
            payload = hit.payload
            # Enforce ownership again at the response boundary in case an adapter
            # or test double returns points outside the mandatory Qdrant filter.
            if (
                payload.get("user_id") != context.user_id
                or payload.get("knowledge_base_id") != context.knowledge_base_id
                or payload.get("document_version_id") not in context.active_version_ids
                or payload.get("index_status") != "active"
            ):
                continue
            if context.score_threshold is not None and hit.score < context.score_threshold:
                continue
            try:
                results.append(self._to_result(payload, float(hit.score)))
            except (TypeError, ValueError) as exc:
                raise DenseRetrievalError("Qdrant returned a malformed document chunk") from exc
        return results

    @staticmethod
    def _to_result(payload: Mapping[str, Any], score: float) -> RetrievalResult:
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        page_numbers = payload.get("page_numbers", ())
        heading_path = payload.get("heading_path", ())
        return RetrievalResult(
            document_id=str(payload["document_id"]),
            document_version_id=str(payload["document_version_id"]),
            chunk_id=str(payload["chunk_id"]),
            content=str(payload["content"]),
            score=score,
            page_numbers=tuple(int(page) for page in page_numbers),
            heading_path=tuple(str(part) for part in heading_path),
            knowledge_base_id=str(payload["knowledge_base_id"]),
            metadata=metadata,
        )
