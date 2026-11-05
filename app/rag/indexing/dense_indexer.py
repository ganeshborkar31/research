"""Dense point operations for the existing Qdrant document collection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models

from app.core.config import get_settings
from app.rag.indexing.errors import IndexCollectionError, PartialIndexingError
from app.rag.indexing.models import DocumentIndexScope
from app.rag.ports.vector_store import VectorRecord


class DenseDocumentIndexer:
    """Use an already-provisioned, unnamed dense collection; never create collections."""

    def __init__(
        self,
        *,
        client: AsyncQdrantClient | None = None,
        collection_name: str = "user_documents_v1",
        batch_size: int | None = None,
    ) -> None:
        settings = get_settings()
        self.collection_name = collection_name
        self.batch_size = batch_size or settings.qdrant_batch_size
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self._owns_client = client is None
        self._client = client or AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
            timeout=settings.qdrant_timeout_seconds,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.close()

    @staticmethod
    def point_id(scope: DocumentIndexScope, chunk_id: str) -> str:
        identity = {
            "knowledge_base_id": scope.knowledge_base_id,
            "document_id": scope.document_id,
            "document_version_id": scope.document_version_id,
            "chunk_id": chunk_id,
        }
        return str(uuid5(NAMESPACE_URL, json.dumps(identity, sort_keys=True, separators=(",", ":"))))

    @staticmethod
    def content_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def validate_collection(self, *, expected_dimensions: int | None = None) -> int:
        try:
            if not await self._client.collection_exists(self.collection_name):
                raise IndexCollectionError(
                    f"Qdrant collection {self.collection_name!r} does not exist; indexing will not create it"
                )
            info = await self._client.get_collection(self.collection_name)
        except IndexCollectionError:
            raise
        except Exception as exc:
            raise IndexCollectionError("Unable to inspect the configured Qdrant collection") from exc

        vectors = info.config.params.vectors
        if isinstance(vectors, dict) or not hasattr(vectors, "size"):
            raise IndexCollectionError("Indexing requires the existing unnamed dense-vector configuration")
        if expected_dimensions is not None and vectors.size != expected_dimensions:
            raise IndexCollectionError(
                f"Embedding dimension {expected_dimensions} does not match existing collection "
                f"dimension {vectors.size}"
            )
        return vectors.size

    @staticmethod
    def scope_filter(
        scope: DocumentIndexScope,
        *,
        version_id: str | None = None,
        exclude_version_id: str | None = None,
        point_ids: Sequence[str] | None = None,
    ) -> models.Filter:
        conditions = [
            models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in (
                ("knowledge_base_id", scope.knowledge_base_id),
                ("document_id", scope.document_id),
                ("user_id", scope.user_id),
            )
        ]
        if version_id is not None:
            conditions.append(
                models.FieldCondition(
                    key="document_version_id", match=models.MatchValue(value=version_id)
                )
            )
        if point_ids is not None:
            if not point_ids:
                raise ValueError("point_ids must not be empty when supplied")
            conditions.append(models.HasIdCondition(has_id=list(point_ids)))
        excluded = []
        if exclude_version_id is not None:
            excluded.append(
                models.FieldCondition(
                    key="document_version_id", match=models.MatchValue(value=exclude_version_id)
                )
            )
        return models.Filter(must=conditions, must_not=excluded or None)

    async def upsert(self, records: Sequence[VectorRecord]) -> None:
        completed = 0
        for offset in range(0, len(records), self.batch_size):
            batch = records[offset:offset + self.batch_size]
            points = [
                models.PointStruct(id=record.id, vector=record.vector, payload=record.payload)
                for record in batch
            ]
            try:
                await self._client.upsert(
                    collection_name=self.collection_name, points=points, wait=True
                )
            except Exception as exc:
                raise PartialIndexingError(completed, len(records)) from exc
            completed += len(batch)

    async def scroll(self, point_filter: models.Filter) -> list[object]:
        points: list[object] = []
        offset = None
        while True:
            try:
                batch, offset = await self._client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=point_filter,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:
                raise IndexCollectionError("Unable to read scoped points from Qdrant") from exc
            points.extend(batch)
            if offset is None:
                return points

    async def set_status(self, point_filter: models.Filter, status: str) -> None:
        await self._client.set_payload(
            collection_name=self.collection_name,
            payload={"index_status": status},
            points=point_filter,
            wait=True,
        )

    async def delete_ids(self, point_ids: Sequence[str]) -> None:
        for offset in range(0, len(point_ids), self.batch_size):
            await self._client.delete(
                collection_name=self.collection_name,
                points_selector=models.PointIdsList(
                    points=list(point_ids[offset:offset + self.batch_size])
                ),
                wait=True,
            )
