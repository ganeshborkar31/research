"""Failure-safe orchestration for document chunk indexing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.rag.embeddings.user_memory_compat import embed_user_memory_text
from app.rag.indexing.dense_indexer import DenseDocumentIndexer
from app.rag.indexing.errors import (
    DocumentIndexStateError,
    DocumentVersionConflict,
    StaleChunkCleanupError,
)
from app.rag.indexing.models import DocumentIndexScope, IndexingResult
from app.rag.models.chunk import DocumentChunk
from app.rag.ports.vector_store import VectorRecord


class DocumentIndexingService:
    """Index chunks in the existing dense collection with staged version replacement."""

    def __init__(self, indexer: DenseDocumentIndexer) -> None:
        self.indexer = indexer

    async def index_document(
        self,
        chunks: list[DocumentChunk],
        *,
        scope: DocumentIndexScope,
        source: str = "document",
        metadata: dict[str, Any] | None = None,
    ) -> IndexingResult:
        self._validate_chunks(chunks, scope)
        safe_metadata = self._json_metadata(metadata if metadata is not None else {})
        dimensions = await self.indexer.validate_collection()
        vectors = [embed_user_memory_text(chunk.content, dimensions) for chunk in chunks]
        point_ids = {
            self.indexer.point_id(scope, chunk.id): (chunk, vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        }
        content_hashes = {
            point_id: self.indexer.content_hash(chunk.content)
            for point_id, (chunk, _vector) in point_ids.items()
        }
        version_filter = self.indexer.scope_filter(scope, version_id=scope.document_version_id)
        version_point_filter = self.indexer.scope_filter(
            scope, version_id=scope.document_version_id, point_ids=list(point_ids)
        )
        records = [
            VectorRecord(
                id=point_id,
                vector=vector,
                payload=self._payload(
                    chunk,
                    scope=scope,
                    source=source,
                    document_metadata=safe_metadata,
                    chunk_metadata=self._json_metadata(chunk.metadata),
                    content_hash=content_hashes[point_id],
                    index_status="staging",
                ),
            )
            for point_id, (chunk, vector) in point_ids.items()
        ]
        records_to_upsert = records

        existing = await self.indexer.scroll(version_filter)
        active = [point for point in existing if (point.payload or {}).get("index_status") == "active"]
        if active:
            active_by_id = {str(point.id): point for point in active}
            existing_hashes = {
                point_id: (point.payload or {}).get("content_hash") for point_id, point in active_by_id.items()
            }
            if existing_hashes == content_hashes:
                await self._remove_staging_points(scope, version_filter)
                deleted = await self._remove_old_versions(scope)
                return IndexingResult(
                    scope.document_id, scope.document_version_id, len(chunks), deleted, idempotent=True
                )
            if not set(existing_hashes).issubset(content_hashes) or any(
                content_hashes.get(point_id) != value for point_id, value in existing_hashes.items()
            ):
                raise DocumentVersionConflict(
                    "An active document version cannot be changed in place; use a new version ID"
                )
            # A prior activation may have updated only some points. Keep those
            # active points intact and repair only missing point IDs.
            records_to_upsert = [record for record in records if record.id not in active_by_id]
        if records_to_upsert:
            await self.indexer.upsert(records_to_upsert)

        try:
            await self.indexer.set_status(version_point_filter, "active")
        except Exception as exc:
            raise DocumentIndexStateError(
                "All chunks were written but the new document version could not be activated; prior versions remain"
            ) from exc

        await self._remove_staging_points(scope, version_filter)
        deleted = await self._remove_old_versions(scope)
        return IndexingResult(
            scope.document_id, scope.document_version_id, len(chunks), deleted, idempotent=False
        )

    async def _remove_staging_points(self, scope: DocumentIndexScope, version_filter: Any) -> None:
        try:
            points = await self.indexer.scroll(version_filter)
            staging = [
                point for point in points
                if (point.payload or {}).get("index_status") == "staging"
                and self._belongs_to_version(point.payload or {}, scope)
            ]
            await self.indexer.delete_ids([str(point.id) for point in staging])
        except Exception as exc:
            raise StaleChunkCleanupError(
                "The new version is active, but leftover staged chunks could not be removed"
            ) from exc

    async def _remove_old_versions(self, scope: DocumentIndexScope) -> int:
        stale_filter = self.indexer.scope_filter(
            scope, exclude_version_id=scope.document_version_id
        )
        try:
            stale = await self.indexer.scroll(stale_filter)
        except Exception as exc:
            raise StaleChunkCleanupError(
                "The new version is active, but stale chunks could not be listed for cleanup"
            ) from exc
        scoped = [
            point for point in stale
            if self._belongs_to_replaced_document(point.payload or {}, scope)
        ]
        if not scoped:
            return 0
        try:
            await self.indexer.set_status(stale_filter, "superseded")
            await self.indexer.delete_ids([str(point.id) for point in scoped])
        except Exception as exc:
            raise StaleChunkCleanupError(
                "The new version is active, but stale chunks remain and cleanup should be retried"
            ) from exc
        return len(scoped)

    @staticmethod
    def _belongs_to_replaced_document(payload: dict[str, Any], scope: DocumentIndexScope) -> bool:
        return all(
            payload.get(key) == value
            for key, value in (
                ("knowledge_base_id", scope.knowledge_base_id),
                ("document_id", scope.document_id),
                ("user_id", scope.user_id),
            )
        ) and payload.get("document_version_id") != scope.document_version_id

    @staticmethod
    def _belongs_to_version(payload: dict[str, Any], scope: DocumentIndexScope) -> bool:
        return all(
            payload.get(key) == value
            for key, value in (
                ("knowledge_base_id", scope.knowledge_base_id),
                ("document_id", scope.document_id),
                ("document_version_id", scope.document_version_id),
                ("user_id", scope.user_id),
            )
        )

    def _payload(
        self,
        chunk: DocumentChunk,
        *,
        scope: DocumentIndexScope,
        source: str,
        document_metadata: dict[str, Any],
        chunk_metadata: dict[str, Any],
        content_hash: str,
        index_status: str,
    ) -> dict[str, Any]:
        return {
            # Keep fields used by existing user-document consumers.
            "record_type": "document",
            "content": chunk.content,
            "source": source,
            "chat_id": "__global__",
            "user_id": scope.user_id,
            "document_id": scope.document_id,
            "document_version_id": scope.document_version_id,
            "knowledge_base_id": scope.knowledge_base_id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.ordinal,
            "token_count": chunk.token_count,
            "heading_path": list(chunk.heading_path),
            "page_numbers": list(chunk.page_numbers),
            "content_hash": content_hash,
            "index_status": index_status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                **document_metadata,
                **chunk_metadata,
                "chunk_id": chunk.id,
                "chunk_index": chunk.ordinal,
                "token_count": chunk.token_count,
                "heading_path": list(chunk.heading_path),
                "page_numbers": list(chunk.page_numbers),
            },
        }

    @staticmethod
    def _validate_chunks(chunks: list[DocumentChunk], scope: DocumentIndexScope) -> None:
        if not chunks:
            raise ValueError("Cannot index a document without chunks")
        seen: set[str] = set()
        for chunk in chunks:
            if not isinstance(chunk, DocumentChunk):
                raise TypeError("chunks must contain DocumentChunk values")
            if chunk.id in seen:
                raise ValueError(f"Duplicate chunk ID {chunk.id!r}")
            seen.add(chunk.id)
            if chunk.document_id != scope.document_id or chunk.user_id != scope.user_id:
                raise ValueError("Chunk ownership does not match the document index scope")
            if chunk.knowledge_base_id not in (None, scope.knowledge_base_id):
                raise ValueError("Chunk knowledge base does not match the document index scope")
            if not chunk.content.strip():
                raise ValueError("Chunk content cannot be empty")

    @staticmethod
    def _json_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise TypeError("document metadata must be a JSON object")
        try:
            return json.loads(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("document metadata must contain JSON-compatible values") from exc
