"""Celery entry points for durable RAG ingestion and reconciliation."""

from __future__ import annotations

import asyncio
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models.rag_document import RagDocument, RagDocumentVersion, RagIngestionJob
from app.rag.ingestion.dependencies import _session_factory, build_ingestion_workflow
from app.rag.ingestion.workflow import RetryableIngestionError
from workers.celery_app import celery_app


def build_workflow():
    """Compatibility name retained for worker overrides and deployment wiring."""
    return build_ingestion_workflow()


def _record_worker_setup_failure(job_id: str, exc: Exception) -> None:
    with _session_factory()() as session:
        job = session.scalar(select(RagIngestionJob).where(
            RagIngestionJob.id == job_id
        ).with_for_update())
        if not job or job.status in {"succeeded", "failed", "cancelled"}:
            return
        version = session.get(RagDocumentVersion, job.document_version_id, with_for_update=True)
        document = session.get(RagDocument, job.document_id, with_for_update=True)
        message = f"Worker setup failed ({type(exc).__name__})"
        job.status = "failed"
        job.last_error = message
        job.error_stage = "worker_setup"
        job.lease_until = None
        if version:
            version.status = "failed"
            version.error_message = message
            version.cleanup_pending = True
        if document and document.active_version_id is None:
            document.status = "failed"
        session.commit()


@celery_app.task(
    bind=True,
    name="rag.ingestion.process",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
)
def ingest_document(self, job_id: str) -> dict[str, str]:
    try:
        workflow = build_workflow()
    except Exception as exc:
        _record_worker_setup_failure(job_id, exc)
        raise RuntimeError("RAG ingestion worker could not initialize") from None

    async def run() -> dict[str, str]:
        try:
            return await workflow.process(job_id)
        finally:
            await workflow.close()

    try:
        return asyncio.run(run())
    except RetryableIngestionError as exc:
        delay = min(2 ** max(int(self.request.retries), 1), 300)
        try:
            workflow.record_retry_schedule(job_id, delay)
        except OperationalError:
            pass
        raise self.retry(exc=exc, countdown=delay)
    except OperationalError as exc:
        delay = min(2 ** max(int(self.request.retries), 1), 300)
        raise self.retry(exc=exc, countdown=delay)


@celery_app.task(name="rag.ingestion.reconcile", ignore_result=True)
def reconcile_ingestion() -> dict[str, int]:
    workflow = build_workflow()

    async def run() -> dict[str, int]:
        try:
            return await workflow.reconcile()
        finally:
            await workflow.close()

    return asyncio.run(run())
