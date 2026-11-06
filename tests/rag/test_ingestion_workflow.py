from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from qdrant_client.http import models as qmodels
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import RagDocument, RagDocumentVersion, RagIngestionJob, User
from app.rag.embeddings.base import EmbeddingConfig
from app.rag.embeddings.service import EmbeddedChunk, EmbeddingService
from app.rag.indexing.dense_indexer import DenseDocumentIndexer
from app.rag.ingestion.chunking import ChunkingConfig, StructuralChunker
from app.rag.ingestion.loaders.docling import DocumentParsingError
from app.rag.ingestion.workflow import (
    IngestionOwnershipError,
    RagIngestionWorkflow,
    RetryableIngestionError,
    normalize_document,
)
from app.rag.models.document import DocumentElement, NormalizedDocument
from app.rag.ports.vector_store import VectorRecord


class WordTokenizer:
    def encode(self, text):
        return text.split()

    def decode(self, tokens):
        return " ".join(tokens)


class FakeEmbeddings:
    dimensions = 3
    config = EmbeddingConfig(provider="test", model="test-model", dimensions=3)

    async def embed_documents(self, chunks):
        return [EmbeddedChunk(chunk, [0.1, 0.2, 0.3], "test", "test-model", 3) for chunk in chunks]

    async def close(self):
        return None


class FakeLoader:
    def __init__(self):
        self.fail = False

    def load(self, source):
        if self.fail:
            raise DocumentParsingError("corrupted fixture")
        text = source.read_text()
        return NormalizedDocument(text, (
            DocumentElement("heading", "Policy", page_number=1, level=1),
            DocumentElement("paragraph", text, page_number=2),
        ), {"parser": "fake"})


class FakeIndexer:
    def __init__(self):
        self.points = {}
        self.fail_upsert_once = False
        self.fail_delete = False

    async def validate_collection(self, *, expected_dimensions=None):
        assert expected_dimensions == 3
        return 3

    @staticmethod
    def point_id(scope, chunk_id):
        return DenseDocumentIndexer.point_id(scope, chunk_id)

    @staticmethod
    def scope_filter(scope, **kwargs):
        return DenseDocumentIndexer.scope_filter(scope, **kwargs)

    async def upsert(self, records):
        if self.fail_upsert_once:
            self.fail_upsert_once = False
            if records:
                self.points[records[0].id] = SimpleNamespace(
                    id=records[0].id, payload=records[0].payload, vector=records[0].vector
                )
            raise RuntimeError("temporary Qdrant failure")
        for record in records:
            self.points[record.id] = SimpleNamespace(
                id=record.id, payload=record.payload, vector=record.vector
            )

    @staticmethod
    def _matches(point, point_filter):
        for condition in point_filter.must or []:
            if isinstance(condition, qmodels.FieldCondition):
                if point.payload.get(condition.key) != condition.match.value:
                    return False
            elif isinstance(condition, qmodels.HasIdCondition):
                if str(point.id) not in {str(point_id) for point_id in condition.has_id}:
                    return False
        for condition in point_filter.must_not or []:
            if isinstance(condition, qmodels.FieldCondition):
                if point.payload.get(condition.key) == condition.match.value:
                    return False
        return True

    async def scroll(self, point_filter):
        return [point for point in self.points.values() if self._matches(point, point_filter)]

    async def set_status(self, point_filter, status):
        for point in await self.scroll(point_filter):
            point.payload["index_status"] = status

    async def delete_ids(self, point_ids):
        if self.fail_delete:
            raise RuntimeError("temporary delete failure")
        for point_id in point_ids:
            self.points.pop(str(point_id), None)

    async def close(self):
        return None


class FlakyActivationWorkflow(RagIngestionWorkflow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fail_activation_once = True

    def _activate(self, *args):
        if self.fail_activation_once:
            self.fail_activation_once = False
            raise RuntimeError("database unavailable after Qdrant activation")
        return super()._activate(*args)


def test_normalization_canonicalizes_text_and_preserves_structure():
    source = NormalizedDocument(
        "  Intro\r\n\r\nBody  ",
        (
            DocumentElement(" SECTION_HEADER ", "  Policy\r", level=1, page_number=1,
                            metadata={"source": "docling"}),
            DocumentElement("paragraph", "  Details\r\n here  ", page_number=2),
        ),
        {"parser": "docling", "source_name": "policy.pdf"},
    )

    normalized = normalize_document(source)

    assert normalized.text == "Intro\n\nBody"
    assert [element.kind for element in normalized.elements] == ["section_header", "paragraph"]
    assert [element.text for element in normalized.elements] == ["Policy", "Details\n here"]
    assert normalized.elements[0].level == 1
    assert normalized.elements[0].page_number == 1
    assert normalized.elements[0].metadata == {"source": "docling"}
    assert normalized.metadata == {"parser": "docling", "source_name": "policy.pdf"}


@pytest.fixture
def workflow_env(tmp_path):
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "policy.txt"
    source.write_text("Private policy content for ingestion.")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(User(id="user-1", username="user1", email="user1@example.test",
                         password_hash="hashed", status="active"))
        session.add(User(id="user-2", username="user2", email="user2@example.test",
                         password_hash="hashed", status="active"))
        session.commit()

    loader = FakeLoader()
    indexer = FakeIndexer()
    dispatched = []
    workflow = RagIngestionWorkflow(
        session_factory=factory,
        loader=loader,
        chunker=StructuralChunker(ChunkingConfig(chunk_size=20, overlap=2), tokenizer=WordTokenizer()),
        embeddings=FakeEmbeddings(),
        indexer=indexer,
        source_root=source_root,
        dispatch_task=dispatched.append,
        max_attempts=3,
        lease_seconds=30,
        dispatch_after_seconds=1,
    )
    yield SimpleNamespace(factory=factory, source_root=source_root, source=source,
                           loader=loader, indexer=indexer, dispatched=dispatched,
                           workflow=workflow, engine=engine)
    engine.dispose()


def submit(env, *, user_id="user-1", document_id="doc-1", idempotency_key="submit-1", force=False):
    return env.workflow.submit(
        user_id=user_id,
        knowledge_base_id="kb-1",
        source_ref=str(env.source),
        document_id=document_id,
        idempotency_key=idempotency_key,
        metadata={"filename": "policy.txt"},
        force_reindex=force,
    )


@pytest.mark.asyncio
async def test_task_pipeline_indexes_and_persists_only_after_qdrant_succeeds(workflow_env):
    env = workflow_env
    request = submit(env)
    assert request["status"] == "queued"
    assert env.dispatched == [request["job_id"]]

    result = await env.workflow.process(request["job_id"])
    assert result["status"] == "succeeded"
    with env.factory() as session:
        document = session.get(RagDocument, "doc-1")
        version = session.get(RagDocumentVersion, request["document_version_id"])
        job = session.get(RagIngestionJob, request["job_id"])
        assert document.status == "active"
        assert document.active_version_id == version.id
        assert version.status == "indexed"
        assert job.status == "succeeded"
    assert env.indexer.points
    assert all(p.payload["index_status"] == "active" for p in env.indexer.points.values())
    assert all(p.payload["user_id"] == "user-1" for p in env.indexer.points.values())
    assert all("tenant_id" not in p.payload for p in env.indexer.points.values())


def test_submission_idempotency_and_per_user_ownership(workflow_env):
    env = workflow_env
    first = submit(env)
    duplicate = submit(env)
    assert duplicate["job_id"] == first["job_id"]
    assert len(env.dispatched) == 1
    with pytest.raises(IngestionOwnershipError):
        submit(env, user_id="user-2")


@pytest.mark.asyncio
async def test_partial_qdrant_upsert_retries_with_stable_ids(workflow_env):
    env = workflow_env
    request = submit(env)
    env.indexer.fail_upsert_once = True
    with pytest.raises(RetryableIngestionError):
        await env.workflow.process(request["job_id"])
    first_ids = set(env.indexer.points)
    assert first_ids
    env.workflow.record_retry_schedule(request["job_id"], 0)

    result = await env.workflow.process(request["job_id"])
    assert result["status"] == "succeeded"
    assert first_ids.issubset(env.indexer.points)
    assert len(env.indexer.points) == len(first_ids)


@pytest.mark.asyncio
async def test_qdrant_success_then_postgres_failure_preserves_old_version(workflow_env):
    env = workflow_env
    first = submit(env)
    await env.workflow.process(first["job_id"])
    old_active = env.indexer.points.copy()

    env.source.write_text("Updated policy content for version two.")
    second = submit(env, idempotency_key="submit-2")
    flaky = FlakyActivationWorkflow(
        session_factory=env.factory,
        loader=env.loader,
        chunker=env.workflow.chunker,
        embeddings=FakeEmbeddings(),
        indexer=env.indexer,
        source_root=env.source_root,
        max_attempts=3,
    )
    with pytest.raises(RetryableIngestionError):
        await flaky.process(second["job_id"])
    with env.factory() as session:
        assert session.get(RagDocument, "doc-1").active_version_id == first["document_version_id"]
    assert old_active.keys() <= env.indexer.points.keys()

    flaky.record_retry_schedule(second["job_id"], 0)
    assert (await flaky.process(second["job_id"]))["status"] == "succeeded"
    with env.factory() as session:
        assert session.get(RagDocument, "doc-1").active_version_id == second["document_version_id"]
    assert {p.payload["document_version_id"] for p in env.indexer.points.values()} == {
        second["document_version_id"]
    }


@pytest.mark.asyncio
async def test_duplicate_task_delivery_is_idempotent(workflow_env):
    env = workflow_env
    request = submit(env)
    assert (await env.workflow.process(request["job_id"]))["status"] == "succeeded"
    point_ids = set(env.indexer.points)
    assert (await env.workflow.process(request["job_id"]))["status"] == "skipped"
    assert set(env.indexer.points) == point_ids


@pytest.mark.asyncio
async def test_expired_worker_lease_is_redispatched(workflow_env):
    env = workflow_env
    request = submit(env)
    with env.factory() as session:
        job = session.get(RagIngestionJob, request["job_id"])
        job.status = "running"
        job.lease_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        job.last_dispatched_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        session.commit()
    env.dispatched.clear()

    result = await env.workflow.reconcile()
    assert result["dispatched"] == 1
    assert env.dispatched == [request["job_id"]]
    with env.factory() as session:
        assert session.get(RagIngestionJob, request["job_id"]).status == "queued"


@pytest.mark.asyncio
async def test_permanent_parse_failure_is_recorded_without_retry(workflow_env):
    env = workflow_env
    request = submit(env)
    env.loader.fail = True
    result = await env.workflow.process(request["job_id"])
    assert result["status"] == "failed"
    with env.factory() as session:
        job = session.get(RagIngestionJob, request["job_id"])
        version = session.get(RagDocumentVersion, request["document_version_id"])
        assert job.status == "failed"
        assert job.error_stage == "parsing"
        assert version.error_message


@pytest.mark.asyncio
async def test_document_deletion_is_user_scoped_and_reconciles_vectors(workflow_env):
    env = workflow_env
    request = submit(env)
    await env.workflow.process(request["job_id"])
    with pytest.raises(IngestionOwnershipError):
        await env.workflow.delete_document(document_id="doc-1", user_id="user-2")

    result = await env.workflow.delete_document(document_id="doc-1", user_id="user-1")
    assert result["status"] == "deleted"
    assert not env.indexer.points
    with env.factory() as session:
        assert session.get(RagDocument, "doc-1").status == "deleted"
        assert session.get(RagDocumentVersion, request["document_version_id"]).status == "deleted"


@pytest.mark.asyncio
async def test_broker_failure_leaves_job_for_reconciliation(workflow_env):
    env = workflow_env
    def fail_dispatch(_job_id):
        raise ConnectionError("RabbitMQ unavailable")

    env.workflow.dispatch_task = fail_dispatch
    request = submit(env)
    with env.factory() as session:
        job = session.get(RagIngestionJob, request["job_id"])
        assert job.status == "queued"
        assert job.last_dispatched_at is None
        assert job.error_stage == "dispatch"
    env.dispatched.clear()
    env.workflow.dispatch_task = env.dispatched.append

    result = await env.workflow.reconcile()
    assert result["dispatched"] == 1
    assert env.dispatched == [request["job_id"]]


@pytest.mark.asyncio
async def test_failed_qdrant_delete_is_reconciled(workflow_env):
    env = workflow_env
    request = submit(env)
    await env.workflow.process(request["job_id"])
    env.indexer.fail_delete = True
    result = await env.workflow.delete_document(document_id="doc-1", user_id="user-1")
    assert result["status"] == "deleting"
    with env.factory() as session:
        assert session.get(RagDocument, "doc-1").status == "deleting"

    env.indexer.fail_delete = False
    result = await env.workflow.reconcile()
    assert result["deleted"] == 1
    assert not env.indexer.points
    with env.factory() as session:
        assert session.get(RagDocument, "doc-1").status == "deleted"


@pytest.mark.asyncio
async def test_stale_chunk_cleanup_failure_does_not_revoke_success(workflow_env):
    env = workflow_env
    first = submit(env)
    await env.workflow.process(first["job_id"])
    env.source.write_text("Version two has new searchable content.")
    second = submit(env, idempotency_key="submit-2")
    env.indexer.fail_delete = True
    result = await env.workflow.process(second["job_id"])
    assert result["status"] == "succeeded"
    with env.factory() as session:
        document = session.get(RagDocument, "doc-1")
        assert document.status == "active"
        assert document.active_version_id == second["document_version_id"]
        assert document.cleanup_pending is True

    env.indexer.fail_delete = False
    result = await env.workflow.reconcile()
    assert result["cleaned"] == 1
    assert {point.payload["document_version_id"] for point in env.indexer.points.values()} == {
        second["document_version_id"]
    }
