from __future__ import annotations

from types import SimpleNamespace

import pytest
from qdrant_client.http import models

from app.rag.embeddings.user_memory_compat import embed_user_memory_text
from app.rag.indexing import (
    DenseDocumentIndexer,
    DocumentIndexScope,
    DocumentIndexStateError,
    DocumentIndexingService,
    DocumentVersionConflict,
    IndexCollectionError,
    PartialIndexingError,
    StaleChunkCleanupError,
)
from app.rag.models.chunk import DocumentChunk


class FakeQdrant:
    def __init__(self, *, dimensions=3):
        self.exists = True
        self.dimensions = dimensions
        self.points = {}
        self.upsert_calls = 0
        self.fail_upsert_call = None
        self.fail_activation = False
        self.partial_activation = False
        self.fail_delete = False
        self.collection_creations = 0

    async def collection_exists(self, name):
        return self.exists

    async def get_collection(self, name):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors=SimpleNamespace(size=self.dimensions))
            )
        )

    async def create_collection(self, **kwargs):
        self.collection_creations += 1
        raise AssertionError("document indexing must not create collections")

    async def upsert(self, *, points, **kwargs):
        self.upsert_calls += 1
        if self.fail_upsert_call == self.upsert_calls:
            raise RuntimeError("simulated Qdrant write failure")
        for point in points:
            self.points[str(point.id)] = SimpleNamespace(
                id=str(point.id), payload=dict(point.payload), vector=point.vector
            )

    @staticmethod
    def _matches(point, point_filter):
        payload = point.payload
        for condition in point_filter.must or []:
            if hasattr(condition, "has_id"):
                if str(point.id) not in {str(point_id) for point_id in condition.has_id}:
                    return False
                continue
            if payload.get(condition.key) != condition.match.value:
                return False
        for condition in point_filter.must_not or []:
            if payload.get(condition.key) == condition.match.value:
                return False
        return True

    async def scroll(self, *, scroll_filter=None, limit=256, offset=None, **kwargs):
        matches = [
            point for point in self.points.values()
            if self._matches(point, scroll_filter)
        ]
        start = int(offset or 0)
        end = start + limit
        return matches[start:end], end if end < len(matches) else None

    async def set_payload(self, *, payload, points, **kwargs):
        status = payload["index_status"]
        if status == "active" and self.fail_activation:
            raise RuntimeError("simulated activation failure")
        updated = 0
        for point in self.points.values():
            if self._matches(point, points):
                point.payload.update(payload)
                updated += 1
                if status == "active" and self.partial_activation and updated == 1:
                    self.partial_activation = False
                    raise RuntimeError("simulated partial activation failure")

    async def delete(self, *, points_selector, **kwargs):
        if self.fail_delete:
            raise RuntimeError("simulated Qdrant deletion failure")
        for point_id in points_selector.points:
            self.points.pop(str(point_id), None)


def make_scope(*, kb_id="kb-a", document_id="doc-a", version_id="v1", user_id="user-a"):
    return DocumentIndexScope(
        knowledge_base_id=kb_id,
        document_id=document_id,
        document_version_id=version_id,
        user_id=user_id,
    )


def make_chunks(scope, *contents):
    return [
        DocumentChunk(
            id=f"chunk-{index}",
            content=content,
            token_count=len(content.split()),
            ordinal=index,
            document_id=scope.document_id,
            user_id=scope.user_id,
            knowledge_base_id=scope.knowledge_base_id,
            heading_path=("Policy",),
            page_numbers=(index + 1,),
            metadata={"element_kinds": ("paragraph",)},
        )
        for index, content in enumerate(contents)
    ]


def make_service(client, *, batch_size=2):
    indexer = DenseDocumentIndexer(
        client=client, collection_name="user_documents_v1", batch_size=batch_size
    )
    return DocumentIndexingService(indexer)


@pytest.mark.asyncio
async def test_dense_points_keep_lexical_payload_and_hybrid_compatible_shape():
    client = FakeQdrant()
    service = make_service(client)
    scope = make_scope()

    result = await service.index_document(
        make_chunks(scope, "Password reset policy details"),
        scope=scope,
        source="handbook.pdf",
        metadata={"filename": "handbook.pdf", "media_type": "application/pdf"},
    )

    assert result.indexed_chunks == 1
    assert result.idempotent is False
    assert client.collection_creations == 0
    point = next(iter(client.points.values()))
    assert isinstance(point.vector, list)  # existing unnamed dense vector
    assert point.vector == embed_user_memory_text("Password reset policy details", 3)
    assert point.payload["record_type"] == "document"
    assert point.payload["content"] == "Password reset policy details"
    assert point.payload["source"] == "handbook.pdf"
    assert point.payload["metadata"]["filename"] == "handbook.pdf"
    assert point.payload["index_status"] == "active"
    assert not any(key in point.payload for key in ("sparse", "sparse_vector", "bm25"))


@pytest.mark.asyncio
async def test_indexing_is_idempotent_and_point_ids_are_stable():
    client = FakeQdrant()
    service = make_service(client)
    scope = make_scope()
    chunks = make_chunks(scope, "A stable document chunk")

    first = await service.index_document(chunks, scope=scope)
    first_ids = set(client.points)
    calls = client.upsert_calls
    second = await service.index_document(chunks, scope=scope)

    assert first_ids == set(client.points)
    assert client.upsert_calls == calls
    assert first.indexed_chunks == second.indexed_chunks == 1
    assert second.idempotent is True


@pytest.mark.asyncio
async def test_new_version_activates_before_old_version_cleanup():
    client = FakeQdrant()
    service = make_service(client)
    version_one = make_scope(version_id="v1")
    version_two = make_scope(version_id="v2")

    await service.index_document(make_chunks(version_one, "Old first", "Old second"), scope=version_one)
    result = await service.index_document(make_chunks(version_two, "New content"), scope=version_two)

    assert result.deleted_stale_chunks == 2
    assert len(client.points) == 1
    point = next(iter(client.points.values()))
    assert point.payload["document_version_id"] == "v2"
    assert point.payload["index_status"] == "active"


@pytest.mark.asyncio
async def test_partial_write_failure_keeps_last_active_version():
    client = FakeQdrant()
    service = make_service(client, batch_size=1)
    version_one = make_scope(version_id="v1")
    version_two = make_scope(version_id="v2")
    await service.index_document(make_chunks(version_one, "Previously valid"), scope=version_one)
    old_id = next(iter(client.points))
    client.fail_upsert_call = client.upsert_calls + 2

    with pytest.raises(PartialIndexingError):
        await service.index_document(
            make_chunks(version_two, "New part one", "New part two"), scope=version_two
        )

    assert client.points[old_id].payload["index_status"] == "active"
    assert client.points[old_id].payload["document_version_id"] == "v1"
    assert any(point.payload["index_status"] == "staging" for point in client.points.values())


@pytest.mark.asyncio
async def test_activation_failure_keeps_previous_version_active():
    client = FakeQdrant()
    service = make_service(client)
    version_one = make_scope(version_id="v1")
    version_two = make_scope(version_id="v2")
    await service.index_document(make_chunks(version_one, "Previously valid"), scope=version_one)
    client.fail_activation = True

    with pytest.raises(DocumentIndexStateError):
        await service.index_document(make_chunks(version_two, "Pending content"), scope=version_two)

    states = {
        point.payload["document_version_id"]: point.payload["index_status"]
        for point in client.points.values()
    }
    assert states == {"v1": "active", "v2": "staging"}


@pytest.mark.asyncio
async def test_partial_activation_can_retry_without_replacing_active_points():
    client = FakeQdrant()
    service = make_service(client)
    version_one = make_scope(version_id="v1")
    version_two = make_scope(version_id="v2")
    await service.index_document(make_chunks(version_one, "Previously valid"), scope=version_one)
    client.partial_activation = True
    version_two_chunks = make_chunks(version_two, "Pending one", "Pending two")

    with pytest.raises(DocumentIndexStateError):
        await service.index_document(version_two_chunks, scope=version_two)
    active_before_retry = {
        point.id for point in client.points.values()
        if point.payload["document_version_id"] == "v2" and point.payload["index_status"] == "active"
    }
    assert len(active_before_retry) == 1

    result = await service.index_document(version_two_chunks, scope=version_two)
    active_after_retry = {
        point.id for point in client.points.values()
        if point.payload["document_version_id"] == "v2" and point.payload["index_status"] == "active"
    }
    assert len(active_after_retry) == 2
    assert active_before_retry.issubset(active_after_retry)
    assert result.deleted_stale_chunks == 1


@pytest.mark.asyncio
async def test_stale_cleanup_failure_keeps_new_version_and_can_retry():
    client = FakeQdrant()
    service = make_service(client)
    version_one = make_scope(version_id="v1")
    version_two = make_scope(version_id="v2")
    await service.index_document(make_chunks(version_one, "Old version"), scope=version_one)
    client.fail_delete = True

    with pytest.raises(StaleChunkCleanupError):
        await service.index_document(make_chunks(version_two, "New version"), scope=version_two)

    assert {point.payload["document_version_id"] for point in client.points.values()} == {"v1", "v2"}
    assert any(
        point.payload["document_version_id"] == "v2" and point.payload["index_status"] == "active"
        for point in client.points.values()
    )
    client.fail_delete = False
    result = await service.index_document(make_chunks(version_two, "New version"), scope=version_two)
    assert result.idempotent is True
    assert result.deleted_stale_chunks == 1
    assert {point.payload["document_version_id"] for point in client.points.values()} == {"v2"}


@pytest.mark.asyncio
async def test_cleanup_is_scoped_to_individual_user_and_document():
    client = FakeQdrant()
    service = make_service(client)
    old_scope = make_scope(version_id="v1")
    other_user = make_scope(user_id="user-b", version_id="v1")

    await service.index_document(make_chunks(old_scope, "Owned old chunk"), scope=old_scope)
    await service.index_document(make_chunks(other_user, "Other user's chunk"), scope=other_user)
    await service.index_document(
        make_chunks(make_scope(version_id="v2"), "Replacement"),
        scope=make_scope(version_id="v2"),
    )

    remaining = list(client.points.values())
    assert {point.payload["user_id"] for point in remaining} == {"user-a", "user-b"}
    assert all(
        not (p.payload["user_id"] == "user-a"
             and p.payload["document_version_id"] == "v1")
        for p in remaining
    )


@pytest.mark.asyncio
async def test_active_version_is_immutable_and_collection_is_never_created():
    client = FakeQdrant()
    service = make_service(client)
    scope = make_scope()
    await service.index_document(make_chunks(scope, "Original text"), scope=scope)

    with pytest.raises(DocumentVersionConflict):
        await service.index_document(make_chunks(scope, "Changed text"), scope=scope)
    assert client.collection_creations == 0


@pytest.mark.asyncio
async def test_missing_or_wrong_dimension_collection_fails_before_writes():
    client = FakeQdrant(dimensions=256)
    indexer = DenseDocumentIndexer(client=client, collection_name="user_documents_v1")

    with pytest.raises(IndexCollectionError, match="does not match existing collection dimension 256"):
        await indexer.validate_collection(expected_dimensions=3)
    assert client.collection_creations == 0

    client.exists = False
    with pytest.raises(IndexCollectionError, match="will not create"):
        await indexer.validate_collection()
    assert client.collection_creations == 0


def test_qdrant_scope_filter_requires_user_kb_document_and_version():
    scope = make_scope()
    point_filter = DenseDocumentIndexer.scope_filter(scope, version_id="v9")
    assert {condition.key for condition in point_filter.must} == {
        "knowledge_base_id", "document_id", "user_id", "document_version_id",
    }
    assert point_filter.must_not in (None, [])
