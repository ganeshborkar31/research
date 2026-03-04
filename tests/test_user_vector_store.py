from types import SimpleNamespace

import pytest

from app.domain.user_memory import UserDocumentIn
from app.services.user_vector_store import UserVectorStore


class FakeQdrantClient:
    def __init__(self):
        self.collections = set()
        self.created = []
        self.indexes = []
        self.upserts = []
        self.queries = []

    async def collection_exists(self, name: str) -> bool:
        return name in self.collections

    async def create_collection(self, collection_name, vectors_config):
        self.collections.add(collection_name)
        self.created.append((collection_name, vectors_config))
        return True

    async def create_payload_index(self, collection_name, field_name, field_schema):
        self.indexes.append((collection_name, field_name, field_schema))
        return SimpleNamespace(status="ok")

    async def upsert(self, collection_name, points):
        self.upserts.append((collection_name, points))
        return SimpleNamespace(status="ok")

    async def query_points(self, **kwargs):
        self.queries.append(kwargs)
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    id="point-1",
                    score=0.91,
                    payload={
                        "content": "User uploaded a contract document",
                        "source": "user_document",
                        "created_at": "2026-03-04T10:00:00+00:00",
                        "metadata": {"doc_type": "contract"},
                    },
                )
            ]
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_store_user_document_and_search():
    client = FakeQdrantClient()
    store = UserVectorStore(client=client, vector_size=8)

    await store.store_user_document(
        UserDocumentIn(
            tenant_id="tenant-1",
            user_id="user-1",
            document_id="doc-1",
            content="My passport and contract details",
            metadata={"classification": "pii"},
        )
    )

    assert len(client.upserts) == 1
    collection_name, points = client.upserts[0]
    assert collection_name == "user_documents_v1"
    assert points[0].payload["tenant_id"] == "tenant-1"
    assert points[0].payload["user_id"] == "user-1"
    assert points[0].payload["record_type"] == "document"

    hits = await store.search_user_documents(
        tenant_id="tenant-1",
        user_id="user-1",
        query="passport",
        limit=3,
    )
    assert len(hits) == 1
    assert hits[0].content == "User uploaded a contract document"
    assert hits[0].metadata["doc_type"] == "contract"


def test_embedding_is_deterministic():
    store = UserVectorStore(client=FakeQdrantClient(), vector_size=8)
    vec1 = store._embed_text("same text")
    vec2 = store._embed_text("same text")
    vec3 = store._embed_text("different text")

    assert len(vec1) == 8
    assert vec1 == vec2
    assert vec1 != vec3

