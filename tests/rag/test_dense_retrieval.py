from types import SimpleNamespace

import pytest

from app.rag.embeddings.base import EmbeddingConfig
from app.rag.embeddings.service import EmbeddingService
from app.rag.models.retrieval import RetrievalQueryContext, RetrievalStrategyName
from app.rag.ports.vector_store import VectorHit
from app.rag.retrieval.semantic import (
    DenseRetrievalConfigurationError,
    DenseRetrievalError,
    DenseRetriever,
)


class FakeEmbeddingProvider:
    dimensions = 3

    def __init__(self):
        self.queries = []

    async def embed_query(self, text):
        self.queries.append(text)
        return [0.1, 0.2, 0.3]

    async def embed_documents(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def close(self):
        pass


class FakeDenseStore:
    vector_size = 3

    def __init__(self, hits=()):
        self.hits = list(hits)
        self.validated_dimensions = []
        self.search_args = None
        self.fail_search = False

    async def validate_vector_dimensions(self, expected_dimensions):
        self.validated_dimensions.append(expected_dimensions)

    async def search_filtered(self, vector, *, limit, query_filter):
        if self.fail_search:
            raise RuntimeError("qdrant unavailable")
        self.search_args = (list(vector), limit, query_filter)
        return self.hits


def make_context(**overrides):
    values = {
        "query": "  test query  ",
        "user_id": "user-a",
        "knowledge_base_id": "kb-a",
        "document_ids": ("doc-a",),
        "metadata_filters": {"department": "engineering"},
        "top_k": 4,
        "score_threshold": 0.7,
        "strategy": RetrievalStrategyName.DENSE,
        "active_version_ids": ("version-a",),
    }
    values.update(overrides)
    return RetrievalQueryContext(**values)


def make_hit(*, user_id="user-a", knowledge_base_id="kb-a", version_id="version-a", score=0.9):
    return VectorHit(
        id="point-a",
        score=score,
        payload={
            "user_id": user_id,
            "knowledge_base_id": knowledge_base_id,
            "document_id": "doc-a",
            "document_version_id": version_id,
            "chunk_id": "chunk-a",
            "content": "A relevant document chunk.",
            "page_numbers": [2, 3],
            "heading_path": ["Guide", "Setup"],
            "metadata": {"department": "engineering", "filename": "guide.pdf"},
            "index_status": "active",
        },
    )


def make_retriever(*, hits=()):
    provider = FakeEmbeddingProvider()
    embeddings = EmbeddingService(
        provider,
        EmbeddingConfig(dimensions=3, retry_backoff_seconds=0),
    )
    store = FakeDenseStore(hits)
    return DenseRetriever(embeddings=embeddings, vector_store=store), provider, store


@pytest.mark.asyncio
async def test_dense_search_reuses_query_embedding_and_preserves_chunk_provenance():
    retriever, provider, store = make_retriever(hits=[make_hit()])
    results = await retriever.retrieve(make_context())

    assert provider.queries == ["  test query  "]
    assert store.validated_dimensions == [3]
    assert store.search_args[0] == [0.1, 0.2, 0.3]
    assert store.search_args[1] == 4
    assert len(results) == 1
    result = results[0]
    assert (result.document_id, result.document_version_id, result.chunk_id) == (
        "doc-a", "version-a", "chunk-a"
    )
    assert result.content == "A relevant document chunk."
    assert result.page_numbers == (2, 3)
    assert result.heading_path == ("Guide", "Setup")
    assert result.knowledge_base_id == "kb-a"
    assert result.metadata["filename"] == "guide.pdf"


@pytest.mark.asyncio
async def test_qdrant_filter_contains_user_kb_document_metadata_and_active_versions():
    retriever, _, store = make_retriever(hits=[make_hit()])
    await retriever.retrieve(make_context())
    filter_conditions = {condition.key: condition for condition in store.search_args[2].must}
    assert filter_conditions["user_id"].match.value == "user-a"
    assert filter_conditions["knowledge_base_id"].match.value == "kb-a"
    assert filter_conditions["document_id"].match.any == ["doc-a"]
    assert filter_conditions["document_version_id"].match.any == ["version-a"]
    assert filter_conditions["index_status"].match.value == "active"
    assert filter_conditions["metadata.department"].match.value == "engineering"


@pytest.mark.asyncio
async def test_threshold_and_user_boundary_are_enforced_on_returned_hits():
    retriever, _, _ = make_retriever(hits=[
        make_hit(score=0.69),
        make_hit(user_id="user-b", score=0.99),
        make_hit(knowledge_base_id="kb-b", score=0.99),
        make_hit(version_id="old-version", score=0.99),
        make_hit(score=0.8),
    ])
    results = await retriever.retrieve(make_context())
    assert [result.score for result in results] == [0.8]


@pytest.mark.asyncio
async def test_empty_scope_and_empty_qdrant_results_return_empty_lists():
    retriever, provider, store = make_retriever()
    assert await retriever.retrieve(make_context(active_version_ids=())) == []
    assert provider.queries == []
    assert store.search_args is None
    assert await retriever.retrieve(make_context()) == []


def test_embedding_and_collection_dimensions_must_match():
    provider = FakeEmbeddingProvider()
    embeddings = EmbeddingService(provider, EmbeddingConfig(dimensions=3))
    with pytest.raises(DenseRetrievalConfigurationError):
        DenseRetriever(embeddings=embeddings, vector_store=SimpleNamespace(vector_size=4))


@pytest.mark.asyncio
async def test_qdrant_failures_are_exposed_as_dense_retrieval_errors():
    retriever, _, store = make_retriever()
    store.fail_search = True
    with pytest.raises(DenseRetrievalError, match="Dense retrieval failed"):
        await retriever.retrieve(make_context())


@pytest.mark.asyncio
async def test_non_dense_strategy_is_rejected():
    retriever, _, _ = make_retriever()
    with pytest.raises(ValueError, match="dense retrieval strategy"):
        await retriever.retrieve(make_context(strategy=RetrievalStrategyName.HYBRID))
