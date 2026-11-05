from types import SimpleNamespace

import pytest

from app.infra.vector_stores.qdrant import QdrantVectorStore
from app.rag.ports.vector_store import VectorRecord


class FakeQdrant:
    def __init__(self):
        self.exists = False
        self.created = []
        self.upserts = []
        self.queries = []
        self.deleted = []

    async def collection_exists(self, name): return self.exists
    async def create_collection(self, **kwargs): self.exists = True; self.created.append(kwargs)
    async def create_payload_index(self, **kwargs): return None
    async def get_collection(self, name):
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=3))))
    async def upsert(self, **kwargs): self.upserts.append(kwargs)
    async def query_points(self, **kwargs):
        self.queries.append(kwargs)
        point = SimpleNamespace(id="id", score=.9, payload={"user_id": "user-1"})
        return SimpleNamespace(points=[point])
    async def retrieve(self, **kwargs): return []
    async def delete(self, **kwargs): self.deleted.append(kwargs)
    async def delete_collection(self, **kwargs): pass
    async def get_collections(self): return SimpleNamespace(collections=[])


@pytest.mark.asyncio
async def test_qdrant_upsert_search_filter_and_delete():
    client = FakeQdrant()
    store = QdrantVectorStore(client=client, vector_size=3, batch_size=1)
    assert store.point_id(user_id="user-1", document_id="d", document_version_id="v", chunk_id="c") == store.point_id(user_id="user-1", document_id="d", document_version_id="v", chunk_id="c")
    await store.ensure_collection()
    assert len(client.created) == 1
    with pytest.raises(ValueError):
        await store.upsert([VectorRecord("bad", [1.0], {"user_id": "user-1", "document_id": "d", "document_version_id": "v"})])
    await store.upsert([VectorRecord("p1", [0.1, 0.2, 0.3], {"user_id": "user-1", "document_id": "d", "document_version_id": "v", "collection_id": "c"})])
    hits = await store.search([0.1, 0.2, 0.3], user_id="user-1", document_id="d", collection_id="c")
    assert hits[0].score == .9
    must = client.queries[0]["query_filter"].must
    assert {c.key for c in must} == {"user_id", "document_id", "collection_id"}
    await store.delete_points(user_id="user-1", document_version_id="v")
    assert client.deleted


@pytest.mark.integration
@pytest.mark.asyncio
async def test_qdrant_local_filtering_and_deletion():
    import os
    if not os.getenv("QDRANT_INTEGRATION_URL"):
        pytest.skip("Set QDRANT_INTEGRATION_URL to run local Qdrant integration test")
    from qdrant_client import AsyncQdrantClient
    client = AsyncQdrantClient(url=os.environ["QDRANT_INTEGRATION_URL"])
    store = QdrantVectorStore(client=client, collection_name="test_rag_adapter", vector_size=3)
    try:
        await store.delete_collection()
        await store.ensure_collection()
        await store.upsert([VectorRecord("p1", [1, 0, 0], {"user_id": "user-1", "document_id": "d1", "document_version_id": "v1", "collection_id": "c1"}),
                            VectorRecord("p2", [1, 0, 0], {"user_id": "user-2", "document_id": "d1", "document_version_id": "v1", "collection_id": "c1"})])
        assert len(await store.search([1, 0, 0], user_id="user-1")) == 1
        await store.delete_points(user_id="user-1", document_id="d1")
        assert len(await store.search([1, 0, 0])) == 1
    finally:
        await store.delete_collection()
        await client.close()


