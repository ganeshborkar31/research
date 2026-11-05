from types import SimpleNamespace

import pytest

from app.rag.dependencies import build_embedding_service
from app.rag.embeddings.base import (
    EmbeddingConfig,
    EmbeddingValidationError,
    InputEmbeddingError,
    PartialEmbeddingError,
    PermanentEmbeddingError,
    TransientEmbeddingError,
)
from app.rag.embeddings.service import EmbeddingService
from app.rag.models.chunk import ChunkReferences, DocumentChunk


class FakeProvider:
    dimensions = 3

    def __init__(self):
        self.calls = []
        self.query_calls = []
        self.failures_remaining = 0
        self.reject_text = None
        self.vectors = None
        self.query_vector = [0.1, 0.2, 0.3]

    async def embed_query(self, text):
        self.query_calls.append(text)
        return self.query_vector

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TimeoutError("temporary provider outage")
        if self.reject_text in texts:
            raise InputEmbeddingError("one input rejected")
        if self.vectors is not None:
            return self.vectors
        return [[float(index), 0.2, 0.3] for index, _ in enumerate(texts)]

    async def close(self):
        pass


def config(**overrides):
    values = {"dimensions": 3, "retry_backoff_seconds": 0, "max_retries": 2}
    values.update(overrides)
    return EmbeddingConfig(**values)


def make_chunk(index, text):
    refs = ChunkReferences("doc-1", "user-1", "kb-1")
    return DocumentChunk(
        id=f"chunk-{index}", content=text, token_count=1, ordinal=index,
        document_id=refs.document_id, user_id=refs.user_id,
        knowledge_base_id=refs.knowledge_base_id,
    )


def test_provider_selection_uses_configured_factory():
    settings = SimpleNamespace(
        embedding_provider="google", embedding_model=None, embedding_dimensions=3,
        embedding_batch_size=7, embedding_max_retries=1, embedding_retry_backoff_seconds=0,
        embedding_timeout_seconds=4,
    )
    selected = FakeProvider()
    service = build_embedding_service(settings, provider_builders={"google": lambda: selected})
    assert service.provider is selected
    assert service.config.provider == "google"
    assert service.config.model == "gemini-embedding-001"
    assert service.dimensions == 3  # Provider reports the actual expected dimension.


def test_custom_provider_can_be_registered_without_editing_embedding_service():
    from app.rag.dependencies import register_embedding_provider

    settings = SimpleNamespace(
        embedding_provider="custom-test", embedding_model="custom-model", embedding_dimensions=3,
        embedding_batch_size=7, embedding_max_retries=1, embedding_retry_backoff_seconds=0,
        embedding_timeout_seconds=4,
    )
    selected = FakeProvider()
    register_embedding_provider("custom-test", lambda: selected)
    try:
        service = build_embedding_service(settings)
        assert service.provider is selected
        assert service.config.model == "custom-model"
    finally:
        from app.rag.dependencies import _CUSTOM_PROVIDER_BUILDERS
        _CUSTOM_PROVIDER_BUILDERS.pop("custom-test", None)


@pytest.mark.asyncio
async def test_document_batching_and_query_use_same_provider_instance():
    provider = FakeProvider()
    service = EmbeddingService(provider, config(batch_size=2))
    chunks = [make_chunk(i, f"text {i}") for i in range(5)]
    embedded = await service.embed_documents(chunks)
    query = await service.embed_query("matching query")
    assert [len(batch) for batch in provider.calls] == [2, 2, 1]
    assert [item.chunk.id for item in embedded] == [chunk.id for chunk in chunks]
    assert all(len(item.vector) == 3 for item in embedded)
    assert all(item.dimensions == 3 and item.provider == "openai" for item in embedded)
    assert query == [0.1, 0.2, 0.3]
    assert provider.query_calls == ["matching query"]


def test_rejects_provider_dimension_mismatch():
    with pytest.raises(ValueError, match="does not match provider dimension"):
        EmbeddingService(FakeProvider(), config(dimensions=4))


@pytest.mark.asyncio
async def test_validates_vector_dimension_and_numeric_values():
    provider = FakeProvider()
    provider.query_vector = [0.1, 0.2]
    service = EmbeddingService(provider, config())
    with pytest.raises(EmbeddingValidationError, match="dimension"):
        await service.embed_query("query")
    provider.vectors = [[0.1, float("nan"), 0.3]]
    with pytest.raises(PartialEmbeddingError) as exc:
        await service.embed_documents([make_chunk(0, "bad vector")])
    assert exc.value.failed_indices == (0,)


@pytest.mark.asyncio
async def test_retries_temporary_failures_and_then_succeeds():
    provider = FakeProvider()
    provider.failures_remaining = 2
    service = EmbeddingService(provider, config(max_retries=2))
    result = await service.embed_texts(["recoverable text"])
    assert len(result) == 1
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_exhausted_transient_failure_is_reported_clearly():
    provider = FakeProvider()
    provider.failures_remaining = 5
    service = EmbeddingService(provider, config(max_retries=1))
    with pytest.raises(TransientEmbeddingError, match="after 2 attempts"):
        await service.embed_texts(["temporarily unavailable"])


@pytest.mark.asyncio
async def test_partial_batch_failure_reports_indices_without_returning_partial_vectors():
    provider = FakeProvider()
    provider.reject_text = "bad input"
    service = EmbeddingService(provider, config(batch_size=3))
    with pytest.raises(PartialEmbeddingError) as exc:
        await service.embed_texts(["first", "bad input", "third"])
    assert exc.value.failed_indices == (1,)
    assert len(provider.calls) > 1  # The failed batch was split to isolate the bad input.


@pytest.mark.asyncio
async def test_wrong_batch_response_count_fails_closed():
    provider = FakeProvider()
    provider.vectors = [[0.1, 0.2, 0.3]]
    service = EmbeddingService(provider, config())
    with pytest.raises(PartialEmbeddingError) as exc:
        await service.embed_documents([make_chunk(0, "one"), make_chunk(1, "two")])
    assert exc.value.failed_indices == (0, 1)


@pytest.mark.asyncio
async def test_permanent_provider_failure_is_not_retried():
    class BrokenProvider(FakeProvider):
        async def embed_documents(self, texts):
            self.calls.append(list(texts))
            raise PermanentEmbeddingError("invalid model")

    provider = BrokenProvider()
    service = EmbeddingService(provider, config(max_retries=3))
    with pytest.raises(PermanentEmbeddingError):
        await service.embed_texts(["text"])
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_rejects_empty_document_text_before_provider_call():
    provider = FakeProvider()
    service = EmbeddingService(provider, config())
    with pytest.raises(ValueError, match="non-empty"):
        await service.embed_texts([" "])
    assert provider.calls == []
