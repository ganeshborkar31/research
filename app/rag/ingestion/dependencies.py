"""Production construction for the durable RAG ingestion workflow."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.rag.dependencies import build_embedding_service
from app.rag.indexing.dense_indexer import DenseDocumentIndexer
from app.rag.ingestion.chunking import ChunkingConfig, StructuralChunker
from app.rag.ingestion.loaders import DoclingDocumentLoader
from app.rag.ingestion.workflow import RagIngestionWorkflow


@lru_cache(maxsize=1)
def _session_factory():
    settings = get_settings()
    if not settings.sync_postgres_url:
        raise RuntimeError("SYNC_POSTGRES_URL is required by the RAG ingestion workflow")
    engine = create_engine(settings.sync_postgres_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _dispatch(job_id: str) -> None:
    from workers.tasks.ingestion import ingest_document

    ingest_document.apply_async(args=[job_id], task_id=job_id)


def build_ingestion_workflow() -> RagIngestionWorkflow:
    settings = get_settings()
    return RagIngestionWorkflow(
        session_factory=_session_factory(),
        loader=DoclingDocumentLoader(),
        chunker=StructuralChunker(ChunkingConfig(
            chunk_size=settings.rag_ingestion_chunk_size,
            overlap=settings.rag_ingestion_chunk_overlap,
        )),
        embeddings=build_embedding_service(settings),
        indexer=DenseDocumentIndexer(collection_name=settings.qdrant_collection_name),
        source_root=settings.rag_source_root,
        dispatch_task=_dispatch,
        max_attempts=settings.rag_ingestion_max_retries + 1,
        lease_seconds=settings.rag_ingestion_lease_seconds,
        dispatch_after_seconds=settings.rag_ingestion_dispatch_after_seconds,
    )
