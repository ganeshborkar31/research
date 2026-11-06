"""Qdrant adapter for versioned, user-scoped RAG chunks."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.core.config import get_settings
from app.rag.models.retrieval import RetrievalQueryContext
from app.rag.ports.vector_store import VectorHit, VectorRecord

logger = logging.getLogger(__name__)


class VectorStoreError(RuntimeError):
    """Qdrant operation failed; callers may retry transient failures."""


class EmptyRetrievalScope(ValueError):
    """No authorized active versions exist; callers must return no results."""


class QdrantVectorStore:
    def __init__(self, *, client: Any | None = None, collection_name: str | None = None,
                 vector_size: int | None = None, timeout: float | None = None,
                 batch_size: int | None = None, api_key: str | None = None) -> None:
        settings = get_settings()
        self.collection_name = collection_name or settings.qdrant_collection_name
        self.vector_size = vector_size or settings.qdrant_vector_size
        self.timeout = timeout or settings.qdrant_timeout_seconds
        self.batch_size = batch_size or settings.qdrant_batch_size
        if self.vector_size <= 0 or self.batch_size <= 0:
            raise ValueError("vector_size and batch_size must be positive")
        self._owns_client = client is None
        self._client = client or AsyncQdrantClient(
            url=settings.qdrant_url, api_key=api_key or settings.qdrant_api_key,
            timeout=self.timeout,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    @staticmethod
    def build_retrieval_filter(context: RetrievalQueryContext) -> models.Filter:
        """Build mandatory user/KB/version filters plus validated caller filters.

        An empty active-version allowlist is rejected so it can never become an
        unfiltered Qdrant request. This only constructs a filter; it does not search.
        """
        if not context.active_version_ids:
            raise EmptyRetrievalScope("No active documents are available in this scope")

        must: list[models.Condition] = [
            models.FieldCondition(
                key="user_id", match=models.MatchValue(value=context.user_id)
            ),
            models.FieldCondition(
                key="knowledge_base_id",
                match=models.MatchValue(value=context.knowledge_base_id),
            ),
            models.FieldCondition(
                key="document_version_id",
                match=models.MatchAny(any=list(context.active_version_ids)),
            ),
            models.FieldCondition(
                key="index_status", match=models.MatchValue(value="active")
            ),
        ]
        if context.document_ids:
            must.append(models.FieldCondition(
                key="document_id", match=models.MatchAny(any=list(context.document_ids))
            ))
        for path, value in context.metadata_filters.items():
            key = f"metadata.{path}"
            if isinstance(value, tuple):
                must.append(models.FieldCondition(
                    key=key, match=models.MatchAny(any=list(value))
                ))
            else:
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        return models.Filter(must=must)

    async def __aenter__(self) -> "QdrantVectorStore":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    @staticmethod
    def point_id(*, user_id: str, document_id: str, document_version_id: str,
                 chunk_id: str) -> str:
        key = "\0".join((user_id, document_id, document_version_id, chunk_id))
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    async def collection_exists(self, name: str | None = None) -> bool:
        try:
            return bool(await self._client.collection_exists(name or self.collection_name))
        except Exception as exc:
            raise VectorStoreError("Unable to check Qdrant collection") from exc

    async def ensure_collection(self, *, name: str | None = None,
                                vector_size: int | None = None) -> None:
        collection = name or self.collection_name
        size = vector_size or self.vector_size
        if size != self.vector_size:
            raise ValueError(f"Configured vector dimension is {self.vector_size}, got {size}")
        try:
            if await self._client.collection_exists(collection):
                info = await self._client.get_collection(collection)
                configured = info.config.params.vectors
                if isinstance(configured, dict):
                    configured = next(iter(configured.values()))
                if configured.size != size:
                    raise ValueError(f"Collection {collection!r} dimension {configured.size} != {size}")
                return
            await self._client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(size=size, distance=models.Distance.COSINE),
            )
            for field in ("user_id", "document_id", "document_version_id", "collection_id"):
                await self._client.create_payload_index(
                    collection_name=collection, field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
            logger.info("Created Qdrant collection %s", collection)
        except ValueError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"Unable to ensure Qdrant collection {collection!r}") from exc

    @staticmethod
    def _validate(record: VectorRecord, size: int) -> None:
        if len(record.vector) != size:
            raise ValueError(f"Vector dimension {len(record.vector)} does not match {size}")
        if not all(isinstance(v, (int, float)) for v in record.vector):
            raise ValueError("Vector values must be numeric")

    async def upsert(self, records: Sequence[VectorRecord], *, collection_id: str | None = None) -> int:
        if not records:
            return 0
        points = []
        for record in records:
            self._validate(record, self.vector_size)
            payload = dict(record.payload)
            if collection_id is not None:
                payload["collection_id"] = collection_id
            required = ("user_id", "document_id", "document_version_id")
            if any(not payload.get(key) for key in required):
                raise ValueError(f"Point payload must include {', '.join(required)}")
            points.append(models.PointStruct(id=record.id, vector=record.vector, payload=payload))
        try:
            for start in range(0, len(points), self.batch_size):
                await self._client.upsert(collection_name=self.collection_name,
                                          points=points[start:start + self.batch_size], wait=True)
            logger.info("Upserted %d Qdrant points", len(points))
            return len(points)
        except Exception as exc:
            logger.exception("Qdrant batch upsert failed")
            raise VectorStoreError("Qdrant batch upsert failed") from exc

    async def get(self, point_id: str) -> VectorHit | None:
        try:
            points = await self._client.retrieve(collection_name=self.collection_name,
                                                 ids=[point_id], with_payload=True)
            if not points:
                return None
            point = points[0]
            return VectorHit(id=str(point.id), score=1.0, payload=point.payload or {})
        except Exception as exc:
            raise VectorStoreError("Qdrant point retrieval failed") from exc

    @staticmethod
    def _filter(*, user_id: str | None, document_id: str | None,
                document_version_id: str | None, collection_id: str | None) -> models.Filter | None:
        conditions = [models.FieldCondition(key=key, match=models.MatchValue(value=value))
                      for key, value in (("user_id", user_id), ("document_id", document_id),
                                         ("document_version_id", document_version_id),
                                         ("collection_id", collection_id)) if value is not None]
        return models.Filter(must=conditions) if conditions else None

    async def search(self, vector: Sequence[float], *, limit: int = 10,
                     user_id: str | None = None, document_id: str | None = None,
                     document_version_id: str | None = None, collection_id: str | None = None,
                     with_payload: bool = True) -> list[VectorHit]:
        self._validate(VectorRecord("query", list(vector)), self.vector_size)
        if limit <= 0:
            raise ValueError("limit must be positive")
        try:
            result = await self._client.query_points(
                collection_name=self.collection_name, query=list(vector), limit=limit,
                query_filter=self._filter(user_id=user_id, document_id=document_id,
                                          document_version_id=document_version_id,
                                          collection_id=collection_id),
                with_payload=with_payload,
            )
            return [VectorHit(id=str(p.id), score=float(p.score), payload=p.payload or {})
                    for p in result.points]
        except Exception as exc:
            raise VectorStoreError("Qdrant similarity search failed") from exc

    async def delete_points(self, *, point_ids: Iterable[str] | None = None,
                            user_id: str | None = None, document_id: str | None = None,
                            document_version_id: str | None = None,
                            collection_id: str | None = None) -> None:
        ids = list(point_ids or [])
        selector: Any
        if ids:
            selector = models.PointIdsList(points=ids)
        else:
            point_filter = self._filter(user_id=user_id, document_id=document_id,
                                        document_version_id=document_version_id,
                                        collection_id=collection_id)
            if point_filter is None:
                raise ValueError("Deletion requires point_ids or at least one filter")
            selector = models.FilterSelector(filter=point_filter)
        try:
            await self._client.delete(collection_name=self.collection_name, points_selector=selector, wait=True)
        except Exception as exc:
            raise VectorStoreError("Qdrant point deletion failed") from exc

    async def delete_collection(self, name: str | None = None) -> None:
        try:
            await self._client.delete_collection(collection_name=name or self.collection_name)
        except Exception as exc:
            raise VectorStoreError("Qdrant collection deletion failed") from exc

    async def health_check(self) -> bool:
        try:
            await self._client.get_collections()
            return True
        except Exception:
            logger.exception("Qdrant health check failed")
            return False
