"""Durable, user-scoped orchestration for asynchronous RAG ingestion."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.rag_document import RagDocument, RagDocumentVersion, RagIngestionJob
from app.rag.embeddings.base import PartialEmbeddingError, PermanentEmbeddingError
from app.rag.embeddings.service import EmbeddingService
from app.rag.indexing.dense_indexer import DenseDocumentIndexer
from app.rag.indexing.errors import IndexCollectionConfigurationError
from app.rag.indexing.models import DocumentIndexScope
from app.rag.ingestion.chunking.base import ChunkingError
from app.rag.ingestion.chunking.structural import StructuralChunker
from app.rag.ingestion.loaders.docling import DocumentLoadError
from app.rag.models.chunk import ChunkReferences
from app.rag.models.document import DocumentElement, NormalizedDocument
from app.rag.ports.vector_store import VectorRecord

logger = logging.getLogger(__name__)


class IngestionOwnershipError(ValueError):
    """The authenticated user does not own the requested document."""


class IngestionNotFoundError(LookupError):
    """A document or ingestion job does not exist in the caller's scope."""


class PermanentIngestionError(RuntimeError):
    """A source or document cannot be ingested and should not be retried."""


class RetryableIngestionError(RuntimeError):
    """An infrastructure failure may succeed on a later task attempt."""


def normalize_document(document: NormalizedDocument) -> NormalizedDocument:
    """Normalize extracted text while retaining structural provenance."""
    if not isinstance(document, NormalizedDocument):
        raise PermanentIngestionError("Loader returned an invalid normalized document")
    if (
        not isinstance(document.text, str)
        or not isinstance(document.elements, tuple)
        or not isinstance(document.metadata, dict)
        or any(
            not isinstance(element, DocumentElement)
            or not isinstance(element.kind, str)
            or not isinstance(element.text, str)
            or not isinstance(element.metadata, dict)
            for element in document.elements
        )
    ):
        raise PermanentIngestionError("Loader returned malformed document structure")
    text = document.text.replace("\r\n", "\n").replace("\r", "\n").strip()
    elements = tuple(
        DocumentElement(
            kind=element.kind.strip().lower(),
            text=element.text.replace("\r\n", "\n").replace("\r", "\n").strip(),
            page_number=element.page_number,
            level=element.level,
            caption=element.caption,
            image_ref=element.image_ref,
            metadata=dict(element.metadata),
        )
        for element in document.elements
    )
    if not text and elements:
        text = "\n\n".join(element.text for element in elements if element.text)
    metadata = json.loads(json.dumps(document.metadata, ensure_ascii=False, allow_nan=False))
    return NormalizedDocument(text=text, elements=elements, metadata=metadata)


class RagIngestionWorkflow:
    """Coordinate durable PostgreSQL state with idempotent Qdrant writes."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        loader: Any,
        chunker: StructuralChunker,
        embeddings: EmbeddingService,
        indexer: DenseDocumentIndexer,
        source_root: str | Path,
        dispatch_task: Callable[[str], None] | None = None,
        max_attempts: int = 6,
        lease_seconds: int = 900,
        dispatch_after_seconds: int = 60,
    ) -> None:
        self.sessions = session_factory
        self.loader = loader
        self.chunker = chunker
        self.embeddings = embeddings
        self.indexer = indexer
        self.source_root = Path(source_root).expanduser().resolve()
        self.dispatch_task = dispatch_task
        if max_attempts < 1 or lease_seconds < 1 or dispatch_after_seconds < 1:
            raise ValueError("retry, lease, and dispatch settings must be positive")
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds
        self.dispatch_after_seconds = dispatch_after_seconds

    def submit(
        self,
        *,
        user_id: str,
        knowledge_base_id: str,
        source_ref: str,
        document_id: str | None = None,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
        force_reindex: bool = False,
    ) -> dict[str, str | int]:
        """Create document/version/job in one transaction, then dispatch durably."""
        self._required(user_id, "user_id")
        self._required(knowledge_base_id, "knowledge_base_id")
        source_path = self._resolve_source(source_ref)
        checksum = self._checksum(source_path)
        document_id = (document_id or str(uuid4())).strip()
        self._required(document_id, "document_id")
        metadata_value = self._metadata(metadata if metadata is not None else {})
        idempotency_key = idempotency_key or hashlib.sha256(
            f"{user_id}\0{document_id}\0{checksum}\0{uuid4() if force_reindex else ''}".encode()
        ).hexdigest()
        self._required(idempotency_key, "idempotency_key")

        with self.sessions() as session:
            duplicate = session.scalar(
                select(RagIngestionJob).where(
                    RagIngestionJob.user_id == user_id,
                    RagIngestionJob.idempotency_key == idempotency_key,
                )
            )
            if duplicate:
                return self._job_result(session, duplicate)

            document = session.scalar(
                select(RagDocument).where(RagDocument.id == document_id).with_for_update()
            )
            if document and document.user_id != user_id:
                raise IngestionOwnershipError("Document is owned by another user")
            if document and document.status in {"deleting", "deleted"}:
                raise PermanentIngestionError("Deleted documents cannot be reingested")
            if document and document.knowledge_base_id != knowledge_base_id:
                raise IngestionOwnershipError("Document does not belong to this knowledge base")

            if document and not force_reindex:
                indexed = session.scalar(
                    select(RagDocumentVersion).where(
                        RagDocumentVersion.id == document.active_version_id,
                        RagDocumentVersion.content_checksum == checksum,
                        RagDocumentVersion.status == "indexed",
                    )
                )
                if indexed:
                    old_job = session.scalar(
                        select(RagIngestionJob).where(
                            RagIngestionJob.document_version_id == indexed.id,
                            RagIngestionJob.user_id == user_id,
                        )
                    )
                    if old_job:
                        return self._job_result(session, old_job)

            if document is None:
                document = RagDocument(
                    id=document_id,
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                    source_ref=str(source_path),
                    status="queued",
                )
                session.add(document)
                session.flush()
            else:
                document.source_ref = str(source_path)

            latest_version = session.scalar(
                select(func.max(RagDocumentVersion.version_number)).where(
                    RagDocumentVersion.document_id == document_id
                )
            ) or 0
            version = RagDocumentVersion(
                id=str(uuid4()),
                document_id=document_id,
                user_id=user_id,
                version_number=latest_version + 1,
                source_ref=str(source_path),
                content_checksum=checksum,
                status="queued",
                metadata_json=metadata_value,
            )
            session.add(version)
            session.flush()
            job = RagIngestionJob(
                id=str(uuid4()),
                document_id=document_id,
                document_version_id=version.id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                status="queued",
                attempts=0,
                max_attempts=self.max_attempts,
            )
            session.add(job)
            session.commit()
            result = {"document_id": document_id, "document_version_id": version.id,
                      "job_id": job.id, "status": job.status, "version_number": version.version_number}

        self._dispatch(result["job_id"])
        return result

    async def process(self, job_id: str) -> dict[str, str]:
        claim = self._claim(job_id)
        if claim is None:
            return {"job_id": job_id, "status": "skipped"}
        heartbeat = asyncio.create_task(self._heartbeat(job_id))
        try:
            return await self._process_claimed(claim)
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _process_claimed(
        self, claim: tuple[str, str, str, str, str, str, str, dict[str, Any]]
    ) -> dict[str, str]:
        (job_id, document_id, version_id, user_id, knowledge_base_id,
         source_ref, expected_checksum, metadata) = claim
        stage = "source_validation"
        try:
            source_path = self._resolve_source(source_ref)
            if self._checksum(source_path) != expected_checksum:
                raise PermanentIngestionError("Document source changed after this version was created")
            stage = "parsing"
            parsed = self.loader.load(source_path)
            stage = "normalization"
            normalized = normalize_document(parsed)
            stage = "chunking"
            chunks = self.chunker.chunk(
                normalized,
                ChunkReferences(document_id=document_id, user_id=user_id,
                                knowledge_base_id=knowledge_base_id),
            )
            if not chunks:
                raise PermanentIngestionError("Document produced no searchable chunks")
            stage = "embedding"
            embedded = await self.embeddings.embed_documents(chunks)
            scope = DocumentIndexScope(
                knowledge_base_id=knowledge_base_id,
                document_id=document_id,
                document_version_id=version_id,
                user_id=user_id,
            )
            await self.indexer.validate_collection(expected_dimensions=self.embeddings.dimensions)
            records = [
                VectorRecord(
                    id=self.indexer.point_id(scope, item.chunk.id),
                    vector=item.vector,
                    payload=self._payload(
                        item.chunk, scope=scope, source_ref=source_ref,
                        document_metadata=metadata, parser_metadata=normalized.metadata,
                        provider=item.provider, model=item.model, dimensions=item.dimensions,
                        status="staging",
                    ),
                )
                for item in embedded
            ]
            stage = "qdrant_upsert"
            await self.indexer.upsert(records)
            self._mark_staged(job_id, version_id)

            # Qdrant is made ready before PostgreSQL switches the active pointer.
            # Until that commit, readers must keep using the previous active version.
            stage = "qdrant_activate"
            await self.indexer.set_status(
                self.indexer.scope_filter(scope, version_id=version_id,
                                          point_ids=[record.id for record in records]),
                "active",
            )
            stage = "postgres_activate"
            activated = self._activate(job_id, document_id, version_id)
            if not activated:
                await self._compensate_version(scope, version_id)
                return {"job_id": job_id, "status": "cancelled"}

            # Persist cleanup intent before trying cross-store deletion.
            stage = "stale_cleanup"
            try:
                await self._cleanup_previous_versions(scope, version_id)
                self._set_cleanup_pending(document_id, False)
            except Exception:
                logger.exception("RAG stale point cleanup deferred for document %s", document_id)
            return {"job_id": job_id, "status": "succeeded"}
        except Exception as exc:
            permanent = isinstance(exc, (DocumentLoadError, ChunkingError, PermanentIngestionError,
                                         PermanentEmbeddingError, PartialEmbeddingError,
                                         IndexCollectionConfigurationError,
                                         ValueError, TypeError))
            retry = self._record_failure(job_id, version_id, document_id, stage, exc, permanent=permanent)
            if not retry:
                if self._job_succeeded(job_id):
                    logger.warning("Post-activation cleanup deferred for succeeded job %s", job_id)
                    return {"job_id": job_id, "status": "succeeded"}
                scope = DocumentIndexScope(knowledge_base_id=knowledge_base_id,
                                           document_id=document_id,
                                           document_version_id=version_id, user_id=user_id)
                try:
                    await self._compensate_version(scope, version_id)
                    self._set_version_cleanup_pending(version_id, False)
                except Exception:
                    logger.exception("RAG failed-version cleanup deferred for version %s", version_id)
                    self._set_version_cleanup_pending(version_id, True)
            if retry:
                raise RetryableIngestionError(f"{stage} failed") from None
            return {"job_id": job_id, "status": "failed"}

    def record_retry_schedule(self, job_id: str, countdown: int) -> None:
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id, with_for_update=True)
            if not job or job.status in {"succeeded", "failed", "cancelled"}:
                return
            job.status = "retrying"
            job.lease_until = now + timedelta(seconds=countdown)
            job.last_dispatched_at = now
            session.commit()

    async def delete_document(self, *, document_id: str, user_id: str) -> dict[str, str]:
        with self.sessions() as session:
            document = session.scalar(
                select(RagDocument).where(RagDocument.id == document_id).with_for_update()
            )
            if not document:
                raise IngestionNotFoundError("Document not found")
            if document.user_id != user_id:
                raise IngestionOwnershipError("Document is owned by another user")
            if document.status == "deleted":
                return {"document_id": document_id, "status": "deleted"}
            document.status = "deleting"
            versions = list(session.scalars(select(RagDocumentVersion).where(
                RagDocumentVersion.document_id == document_id,
                RagDocumentVersion.user_id == user_id,
            )))
            for version in versions:
                version.status = "deleting"
            for job in session.scalars(select(RagIngestionJob).where(
                RagIngestionJob.document_id == document_id,
                RagIngestionJob.user_id == user_id,
                RagIngestionJob.status.notin_(("succeeded", "failed", "cancelled")),
            )):
                job.status = "cancelled"
                job.lease_until = None
            session.commit()
        try:
            for version in versions:
                scope = DocumentIndexScope(
                    knowledge_base_id=document.knowledge_base_id, document_id=document_id,
                    document_version_id=version.id, user_id=user_id,
                )
                await self._delete_version_points(scope)
            self._finish_delete(document_id, versions)
            return {"document_id": document_id, "status": "deleted"}
        except Exception:
            logger.exception("RAG document deletion will be reconciled for %s", document_id)
            return {"document_id": document_id, "status": "deleting"}

    async def reconcile(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        stale_dispatch_before = now - timedelta(seconds=self.dispatch_after_seconds)
        with self.sessions() as session:
            jobs = list(session.scalars(select(RagIngestionJob).where(or_(
                and_(RagIngestionJob.status.in_(("queued", "retrying")),
                     or_(RagIngestionJob.last_dispatched_at.is_(None),
                         RagIngestionJob.last_dispatched_at <= stale_dispatch_before),
                     or_(RagIngestionJob.lease_until.is_(None), RagIngestionJob.lease_until <= now)),
                and_(RagIngestionJob.status == "running", RagIngestionJob.lease_until <= now),
            ))))
            expired_ids = [job.id for job in jobs]
            for job in jobs:
                job.status = "queued"
                job.lease_until = None
            session.commit()
            deleting = list(session.scalars(select(RagDocument).where(RagDocument.status == "deleting")))
            cleanup_docs = list(session.scalars(select(RagDocument).where(
                RagDocument.cleanup_pending.is_(True), RagDocument.status == "active"
            )))
            cleanup_versions = list(session.scalars(select(RagDocumentVersion).where(
                RagDocumentVersion.cleanup_pending.is_(True), RagDocumentVersion.status == "failed"
            )))

        dispatched = 0
        for job_id in expired_ids:
            self._dispatch(job_id)
            dispatched += 1
        deleted = 0
        for document in deleting:
            try:
                with self.sessions() as session:
                    versions = list(session.scalars(select(RagDocumentVersion).where(
                        RagDocumentVersion.document_id == document.id,
                        RagDocumentVersion.user_id == document.user_id,
                    )))
                for version in versions:
                    scope = DocumentIndexScope(document.knowledge_base_id, document.id,
                                               version.id, document.user_id)
                    await self._delete_version_points(scope)
                self._finish_delete(document.id, versions)
                deleted += 1
            except Exception:
                logger.exception("RAG deletion reconciliation failed for %s", document.id)
        cleaned = 0
        for document in cleanup_docs:
            try:
                scope = DocumentIndexScope(document.knowledge_base_id, document.id,
                                           document.active_version_id or "none", document.user_id)
                await self._cleanup_previous_versions(scope, document.active_version_id)
                self._set_cleanup_pending(document.id, False)
                cleaned += 1
            except Exception:
                logger.exception("RAG stale cleanup reconciliation failed for %s", document.id)
        for version in cleanup_versions:
            try:
                with self.sessions() as session:
                    document = session.get(RagDocument, version.document_id)
                if document:
                    scope = DocumentIndexScope(document.knowledge_base_id, document.id,
                                               version.id, version.user_id)
                    await self._compensate_version(scope, version.id)
                    self._set_version_cleanup_pending(version.id, False)
                    cleaned += 1
            except Exception:
                logger.exception("RAG failed-version cleanup reconciliation failed for %s", version.id)
        return {"dispatched": dispatched, "deleted": deleted, "cleaned": cleaned}

    def _claim(self, job_id: str) -> tuple[str, str, str, str, str, str, str, dict[str, Any]] | None:
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            job = session.scalar(select(RagIngestionJob).where(RagIngestionJob.id == job_id).with_for_update())
            if not job:
                return None
            if job.status in {"succeeded", "failed", "cancelled"}:
                return None
            if job.status == "running" and job.lease_until and job.lease_until > now:
                return None
            document = session.scalar(select(RagDocument).where(
                RagDocument.id == job.document_id, RagDocument.user_id == job.user_id
            ).with_for_update())
            version = session.scalar(select(RagDocumentVersion).where(
                RagDocumentVersion.id == job.document_version_id,
                RagDocumentVersion.document_id == job.document_id,
                RagDocumentVersion.user_id == job.user_id,
            ).with_for_update())
            if not document or not version or document.status in {"deleting", "deleted"}:
                job.status = "cancelled"
                if version:
                    version.status = "deleting"
                session.commit()
                return None
            another_active_job = session.scalar(select(RagIngestionJob.id).where(
                RagIngestionJob.document_id == document.id,
                RagIngestionJob.user_id == job.user_id,
                RagIngestionJob.id != job.id,
                RagIngestionJob.status == "running",
                RagIngestionJob.lease_until > now,
            ).limit(1))
            if another_active_job:
                return None
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                version.status = "failed"
                version.cleanup_pending = True
                if document.active_version_id is None:
                    document.status = "failed"
                session.commit()
                return None
            job.status = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(seconds=self.lease_seconds)
            job.last_error = None
            job.error_stage = None
            version.status = "processing"
            if document.active_version_id is None:
                document.status = "processing"
            session.commit()
            return (job.id, document.id, version.id, job.user_id,
                    document.knowledge_base_id, version.source_ref, version.content_checksum,
                    dict(version.metadata_json or {}))

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(1, min(60, self.lease_seconds // 3))
        while True:
            await asyncio.sleep(interval)
            if not self._renew_lease(job_id):
                return

    def _renew_lease(self, job_id: str) -> bool:
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id, with_for_update=True)
            if not job or job.status != "running":
                return False
            job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)
            session.commit()
            return True

    def _mark_staged(self, job_id: str, version_id: str) -> None:
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id, with_for_update=True)
            version = session.get(RagDocumentVersion, version_id, with_for_update=True)
            if not job or job.status != "running" or not version:
                raise RetryableIngestionError("Ingestion lease was lost before Qdrant staging")
            version.status = "staged"
            session.commit()

    def _activate(self, job_id: str, document_id: str, version_id: str) -> bool:
        with self.sessions() as session:
            job = session.scalar(select(RagIngestionJob).where(RagIngestionJob.id == job_id).with_for_update())
            document = session.scalar(select(RagDocument).where(RagDocument.id == document_id).with_for_update())
            version = session.scalar(select(RagDocumentVersion).where(RagDocumentVersion.id == version_id).with_for_update())
            if not job or not document or not version or job.status != "running":
                return False
            if document.status in {"deleting", "deleted"} or version.status in {"deleting", "deleted"}:
                job.status = "cancelled"
                session.commit()
                return False
            previous_version_id = document.active_version_id
            if previous_version_id and previous_version_id != version.id:
                previous = session.scalar(select(RagDocumentVersion).where(
                    RagDocumentVersion.id == previous_version_id,
                    RagDocumentVersion.document_id == document.id,
                    RagDocumentVersion.user_id == document.user_id,
                ).with_for_update())
                if previous and previous.status == "indexed":
                    previous.status = "superseded"
            document.active_version_id = version.id
            document.status = "active"
            document.cleanup_pending = True
            version.status = "indexed"
            version.error_message = None
            job.status = "succeeded"
            job.lease_until = None
            job.last_error = None
            job.error_stage = None
            session.commit()
            return True

    def _record_failure(self, job_id: str, version_id: str, document_id: str,
                        stage: str, exc: Exception, *, permanent: bool) -> bool:
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id, with_for_update=True)
            version = session.get(RagDocumentVersion, version_id, with_for_update=True)
            document = session.get(RagDocument, document_id, with_for_update=True)
            if not job or job.status in {"succeeded", "cancelled"}:
                return False
            retry = not permanent and job.attempts < job.max_attempts
            safe_error = self._safe_error(exc)
            job.status = "retrying" if retry else "failed"
            job.lease_until = None
            job.last_error = safe_error
            job.error_stage = stage
            if version:
                version.status = "processing" if retry else "failed"
                version.error_message = safe_error
                if not retry:
                    version.cleanup_pending = True
            if document and document.active_version_id is None and not retry:
                document.status = "failed"
            session.commit()
            return retry

    @staticmethod
    def _payload(chunk: Any, *, scope: DocumentIndexScope, source_ref: str,
                 document_metadata: dict[str, Any], parser_metadata: dict[str, Any],
                 provider: str, model: str | None, dimensions: int, status: str) -> dict[str, Any]:
        return {
            "record_type": "rag_chunk", "content": chunk.content,
            "user_id": scope.user_id, "knowledge_base_id": scope.knowledge_base_id,
            "document_id": scope.document_id, "document_version_id": scope.document_version_id,
            "chunk_id": chunk.id, "chunk_index": chunk.ordinal, "token_count": chunk.token_count,
            "heading_path": list(chunk.heading_path), "page_numbers": list(chunk.page_numbers),
            "source": Path(source_ref).name,
            "metadata": {**document_metadata, "parser": parser_metadata,
                         "chunk": chunk.metadata, "embedding_provider": provider,
                         "embedding_model": model, "embedding_dimensions": dimensions},
            "index_status": status,
        }

    async def _cleanup_previous_versions(self, scope: DocumentIndexScope,
                                         active_version_id: str | None = None) -> None:
        active_version_id = active_version_id or scope.document_version_id
        point_filter = self.indexer.scope_filter(scope, exclude_version_id=active_version_id)
        points = await self.indexer.scroll(point_filter)
        ids = [str(point.id) for point in points]
        if ids:
            await self.indexer.delete_ids(ids)

    async def _compensate_version(self, scope: DocumentIndexScope, version_id: str) -> None:
        point_filter = self.indexer.scope_filter(scope, version_id=version_id)
        points = await self.indexer.scroll(point_filter)
        ids = [str(point.id) for point in points]
        if ids:
            await self.indexer.delete_ids(ids)

    async def _delete_version_points(self, scope: DocumentIndexScope) -> None:
        point_filter = self.indexer.scope_filter(scope, version_id=scope.document_version_id)
        points = await self.indexer.scroll(point_filter)
        ids = [str(point.id) for point in points]
        if ids:
            await self.indexer.delete_ids(ids)

    def _finish_delete(self, document_id: str, versions: list[RagDocumentVersion]) -> None:
        with self.sessions() as session:
            document = session.get(RagDocument, document_id, with_for_update=True)
            if not document:
                return
            for version in versions:
                db_version = session.get(RagDocumentVersion, version.id, with_for_update=True)
                if db_version:
                    db_version.status = "deleted"
                    db_version.cleanup_pending = False
            document.status = "deleted"
            document.active_version_id = None
            document.cleanup_pending = False
            session.commit()

    def _set_cleanup_pending(self, document_id: str, value: bool) -> None:
        with self.sessions() as session:
            document = session.get(RagDocument, document_id, with_for_update=True)
            if document:
                document.cleanup_pending = value
                session.commit()

    def _set_version_cleanup_pending(self, version_id: str, value: bool) -> None:
        with self.sessions() as session:
            version = session.get(RagDocumentVersion, version_id, with_for_update=True)
            if version:
                version.cleanup_pending = value
                session.commit()

    def _job_succeeded(self, job_id: str) -> bool:
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id)
            return bool(job and job.status == "succeeded")

    async def close(self) -> None:
        await self.embeddings.close()
        await self.indexer.close()

    def _dispatch(self, job_id: str) -> None:
        now = datetime.now(timezone.utc)
        if self.dispatch_task is None:
            return
        try:
            self.dispatch_task(job_id)
        except Exception as exc:
            logger.warning("RAG ingestion dispatch deferred for job %s (%s)", job_id, type(exc).__name__)
            with self.sessions() as session:
                job = session.get(RagIngestionJob, job_id, with_for_update=True)
                if job and job.status == "queued":
                    job.last_error = "Broker dispatch failed; pending-job reconciliation will retry"
                    job.error_stage = "dispatch"
                    session.commit()
            return
        with self.sessions() as session:
            job = session.get(RagIngestionJob, job_id, with_for_update=True)
            if job and job.status in {"queued", "retrying"}:
                job.task_id = job.id
                job.last_dispatched_at = now
                if job.error_stage == "dispatch":
                    job.last_error = None
                    job.error_stage = None
                session.commit()

    def _job_result(self, session: Session, job: RagIngestionJob) -> dict[str, str | int]:
        version = session.get(RagDocumentVersion, job.document_version_id)
        return {"document_id": job.document_id, "document_version_id": job.document_version_id,
                "job_id": job.id, "status": job.status,
                "version_number": version.version_number if version else 0}

    def _resolve_source(self, source_ref: str) -> Path:
        if not isinstance(source_ref, str) or not source_ref.strip() or "://" in source_ref:
            raise PermanentIngestionError("Only local document references are supported")
        try:
            root = self.source_root.resolve(strict=True)
            source = Path(source_ref).expanduser()
            if not source.is_absolute():
                source = root / source
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise PermanentIngestionError("Document reference is unavailable") from exc
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PermanentIngestionError("Document reference is outside the configured source directory") from exc
        if not resolved.is_file():
            raise PermanentIngestionError("Document reference must identify a regular file")
        maximum = getattr(getattr(self.loader, "config", None), "max_file_bytes", 50 * 1024 * 1024)
        if resolved.stat().st_size > maximum:
            raise PermanentIngestionError("Document exceeds the configured file size limit")
        return resolved

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _metadata(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("metadata must be a JSON object")
        try:
            return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must contain JSON-compatible values") from exc

    @staticmethod
    def _required(value: str, name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        if isinstance(exc, DocumentLoadError):
            return f"Document parsing failed ({type(exc).__name__})"
        return f"{type(exc).__name__}: ingestion stage failed"

