import pytest

from app.rag.models.retrieval import RetrievalQueryContext, RetrievalStrategyName
from app.rag.retrieval.lexical import (
    LexicalRetrievalConfigurationError,
    LexicalRetriever,
)


def make_context(**overrides):
    values = {
        "query": "term query",
        "user_id": "user-a",
        "knowledge_base_id": "kb-a",
        "document_ids": (),
        "metadata_filters": {"team": "research"},
        "top_k": 7,
        "score_threshold": None,
        "strategy": RetrievalStrategyName.LEXICAL,
        "active_version_ids": ("version-a",),
    }
    values.update(overrides)
    return RetrievalQueryContext(**values)


@pytest.mark.asyncio
async def test_lexical_retrieval_reports_missing_compatible_index():
    with pytest.raises(LexicalRetrievalConfigurationError, match="no compatible lexical index"):
        await LexicalRetriever().retrieve(make_context())


@pytest.mark.asyncio
async def test_lexical_retrieval_returns_empty_for_empty_scope_without_querying():
    assert await LexicalRetriever().retrieve(make_context(active_version_ids=())) == []


@pytest.mark.asyncio
async def test_lexical_retriever_rejects_other_strategies():
    with pytest.raises(ValueError, match="lexical retrieval strategy"):
        await LexicalRetriever().retrieve(make_context(strategy=RetrievalStrategyName.DENSE))
