"""End-to-end contract checks for Phase 2 Qdrant payloads and Phase 3 retrieval."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from qdrant_client.http import models

from app.infra.vector_stores.qdrant import QdrantVectorStore
from app.rag.embeddings.base import EmbeddingConfig
from app.rag.embeddings.service import EmbeddingService
from app.rag.models.retrieval import RetrievalRequest, RetrievalStrategyName
from app.rag.ports.vector_store import VectorRecord
from app.rag.retrieval.context_builder import ContextAssembler
from app.rag.retrieval.query_processor import RetrievalRequestPreparer
from app.rag.retrieval.semantic import DenseRetriever
from app.rag.retrieval.service import RetrievalService


class SharedProvider:
    """Deterministic provider used for both Phase 2 document and Phase 3 query vectors."""

    dimensions = 3
    model = "test-compatible-embedding"

    @staticmethod
    def vector(text: str) -> list[float]:
        lowered = text.casefold()
        if "orchid" in lowered:
            return [1.0, 0.0, 0.0]
        if "saffron" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    async def embed_query(self, text: str) -> list[float]:
        return self.vector(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]


class Phase2Qdrant:
    """Small in-memory Qdrant contract fake; points use the worker's payload shape."""

    def __init__(self, points: list[VectorRecord], *, dimensions: int = 3):
        self.points = points
        self.dimensions = dimensions
        self.search_filters: list[models.Filter] = []
        self.collection = "rag_chunks_v1"

    async def collection_exists(self, name: str) -> bool:
        assert name == self.collection
        return True

    async def get_collection(self, name: str):
        assert name == self.collection
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(
            vectors=SimpleNamespace(size=self.dimensions)
        )))

    @staticmethod
    def matches(payload: dict, query_filter: models.Filter) -> bool:
        for condition in query_filter.must or []:
            value = payload
            for part in condition.key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            match = condition.match
            if isinstance(match, models.MatchValue) and value != match.value:
                return False
            if isinstance(match, models.MatchAny) and value not in match.any:
                return False
        return True

    async def query_points(self, *, query, query_filter, limit, **kwargs):
        assert query_filter.must
        self.search_filters.append(query_filter)
        scored = []
        for point in self.points:
            if not self.matches(point.payload, query_filter):
                continue
            dot = sum(a * b for a, b in zip(query, point.vector, strict=True))
            qnorm = math.sqrt(sum(a * a for a in query))
            pnorm = math.sqrt(sum(a * a for a in point.vector))
            score = dot / (qnorm * pnorm) if qnorm and pnorm else 0.0
            scored.append(SimpleNamespace(id=point.id, score=score, payload=point.payload))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return SimpleNamespace(points=scored[:limit])


class ScopedSession:
    def __init__(self, user_id: str, rows: list[tuple]):
        self.user_id = user_id
        self.rows = rows

    async def execute(self, statement):
        params = statement.compile().params
        assert self.user_id in params.values()
        return SimpleNamespace(all=lambda: self.rows)


def phase2_point(*, user: str, kb: str, document: str, version: str, chunk: str,
                 content: str, source: str, page: int, status: str = "active") -> VectorRecord:
    provider = SharedProvider()
    return VectorRecord(
        id=f"point-{user}-{chunk}",
        vector=provider.vector(content),
        payload={
            "record_type": "rag_chunk",
            "content": content,
            "user_id": user,
            "knowledge_base_id": kb,
            "document_id": document,
            "document_version_id": version,
            "chunk_id": chunk,
            "chunk_index": 0,
            "token_count": len(content.split()),
            "heading_path": ["Reference"],
            "page_numbers": [page],
            "source": source,
            "metadata": {"filename": source, "parser": "fixture", "embedding": {
                "provider": "test", "model": provider.model, "dimensions": 3,
            }},
            "index_status": status,
        },
    )


@pytest.mark.asyncio
async def test_phase2_points_are_retrievable_with_shared_embeddings_and_user_isolation():
    provider = SharedProvider()
    embeddings = EmbeddingService(provider, EmbeddingConfig(
        provider="test", model=provider.model, dimensions=3, retry_backoff_seconds=0,
    ))
    qdrant = Phase2Qdrant([
        phase2_point(user="user-a", kb="kb-a", document="doc-a", version="v-a1",
                     chunk="chunk-a", content="Orchid care guide", source="orchid.pdf", page=3),
        phase2_point(user="user-b", kb="kb-b", document="doc-b", version="v-b1",
                     chunk="chunk-b", content="Saffron growing guide", source="saffron.pdf", page=7),
        phase2_point(user="user-a", kb="kb-a", document="doc-a", version="v-a0",
                     chunk="chunk-old", content="Orchid archived edition", source="old.pdf", page=1),
        phase2_point(user="user-a", kb="kb-a", document="doc-a", version="v-a2",
                     chunk="chunk-staging", content="Orchid draft", source="draft.pdf", page=2,
                     status="staging"),
    ])
    store = QdrantVectorStore(client=qdrant, collection_name="rag_chunks_v1", vector_size=3)
    dense = DenseRetriever(embeddings=embeddings, vector_store=store)
    service = RetrievalService(
        preparer=RetrievalRequestPreparer(),
        strategies={RetrievalStrategyName.DENSE: dense},
        context_assembler=ContextAssembler(),
    )

    async def retrieve(user: str, *, kb: str, version: str, query: str):
        return await service.retrieve(
            RetrievalRequest(query=query, knowledge_base_id=kb),
            authenticated_user_id=user,
            session=ScopedSession(user, [(f"doc-{user[-1]}", version, "active", "indexed")]),
        )

    results_a, context_a = await retrieve("user-a", kb="kb-a", version="v-a1", query="orchid")
    results_b, context_b = await retrieve("user-b", kb="kb-b", version="v-b1", query="saffron")
    assert [item.document_id for item in results_a] == ["doc-a"]
    assert [item.document_version_id for item in results_a] == ["v-a1"]
    assert results_a[0].source_ref == "orchid.pdf"
    assert results_a[0].page_numbers == (3,)
    assert results_a[0].metadata["embedding"]["model"] == provider.model
    assert context_a.text == "Orchid care guide"
    assert [item.document_id for item in results_b] == ["doc-b"]
    assert context_b.text == "Saffron growing guide"
    assert len(qdrant.search_filters) == 2
    for point_filter in qdrant.search_filters:
        conditions = {condition.key: condition for condition in point_filter.must}
        assert {"user_id", "knowledge_base_id", "document_version_id", "index_status"} <= set(conditions)


@pytest.mark.asyncio
async def test_foreign_kb_and_document_id_cannot_expand_scope_or_reach_qdrant():
    provider = SharedProvider()
    qdrant = Phase2Qdrant([phase2_point(
        user="user-b", kb="kb-b", document="doc-b", version="v-b1", chunk="b",
        content="Saffron growing guide", source="saffron.pdf", page=7,
    )])
    service = RetrievalService(
        preparer=RetrievalRequestPreparer(),
        strategies={RetrievalStrategyName.DENSE: DenseRetriever(
            embeddings=EmbeddingService(provider, EmbeddingConfig(
                provider="test", model=provider.model, dimensions=3, retry_backoff_seconds=0,
            )),
            vector_store=QdrantVectorStore(client=qdrant, collection_name="rag_chunks_v1", vector_size=3),
        )},
    )
    with pytest.raises(Exception, match="not accessible"):
        await service.retrieve(
            RetrievalRequest(query="saffron", knowledge_base_id="kb-b", document_ids=("doc-b",)),
            authenticated_user_id="user-a",
            session=ScopedSession("user-a", []),
        )
    assert qdrant.search_filters == []
