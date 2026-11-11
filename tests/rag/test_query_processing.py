from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.rag.stores.qdrant import EmptyRetrievalScope, QdrantVectorStore
from app.rag.retrieval.dependencies import build_retrieval_request_preparer
from app.rag.models.retrieval import (
    RetrievalQueryContext,
    RetrievalRequest,
    RetrievalStrategyName,
)
from app.rag.retrieval.query_processor import (
    QueryProcessingConfig,
    RetrievalAccessDenied,
    RetrievalQueryError,
    RetrievalQueryProcessor,
    RetrievalRequestPreparer,
    RetrievalScopeResolver,
)


def make_request(**overrides):
    values = {"query": "  Keep  Exact CASE, punctuation!  ", "knowledge_base_id": "kb-1"}
    values.update(overrides)
    return RetrievalRequest.model_validate(values)


def test_query_validation_trims_edges_but_preserves_meaningful_content():
    request = make_request()
    assert request.query == "Keep  Exact CASE, punctuation!"
    with pytest.raises(ValidationError):
        make_request(query=" \n ")
    with pytest.raises(ValidationError):
        make_request(query="x" * 20_001)


def test_client_cannot_supply_user_identity_or_invalid_controls():
    with pytest.raises(ValidationError):
        make_request(user_id="victim")
    with pytest.raises(ValidationError):
        make_request(top_k=0)
    with pytest.raises(ValidationError):
        make_request(top_k=True)
    with pytest.raises(ValidationError):
        make_request(score_threshold=float("nan"))
    with pytest.raises(ValidationError):
        make_request(strategy="agentic")
    with pytest.raises(RetrievalQueryError, match="authenticated_user_id"):
        RetrievalQueryProcessor().process(make_request(), authenticated_user_id=" ")


def test_defaults_and_explicit_strategy_controls_are_applied():
    defaults = QueryProcessingConfig()
    assert (defaults.default_top_k, defaults.max_top_k, defaults.default_score_threshold,
            defaults.default_strategy) == (5, 50, None, RetrievalStrategyName.DENSE)

    processor = RetrievalQueryProcessor(QueryProcessingConfig(
        default_top_k=7,
        max_top_k=9,
        default_score_threshold=0.35,
        default_strategy=RetrievalStrategyName.LEXICAL,
    ))
    defaulted = processor.process(make_request(), authenticated_user_id="user-1")
    assert (defaulted.top_k, defaulted.score_threshold, defaulted.strategy) == (
        7, 0.35, RetrievalStrategyName.LEXICAL
    )

    explicit = processor.process(
        make_request(top_k=9, score_threshold=0.8, strategy="hybrid"),
        authenticated_user_id="user-1",
    )
    assert (explicit.top_k, explicit.score_threshold, explicit.strategy) == (
        9, 0.8, RetrievalStrategyName.HYBRID
    )
    with pytest.raises(RetrievalQueryError, match="maximum"):
        processor.process(make_request(top_k=10), authenticated_user_id="user-1")


def test_settings_factory_wires_retrieval_defaults():
    settings = SimpleNamespace(
        rag_retrieval_default_top_k=8,
        rag_retrieval_max_top_k=20,
        rag_retrieval_default_score_threshold=0.25,
        rag_retrieval_default_strategy="hybrid",
    )
    preparer = build_retrieval_request_preparer(settings)
    context = preparer.processor.process(make_request(), authenticated_user_id="user-1")
    assert (context.top_k, context.score_threshold, context.strategy) == (
        8, 0.25, RetrievalStrategyName.HYBRID
    )


def test_filter_validation_accepts_metadata_scalars_and_rejects_unsafe_combinations():
    request = make_request(
        document_ids=["doc-1", "doc-2"],
        metadata_filters={"department.name": "Research", "page": 3, "labels": ["a", "b"]},
    )
    assert request.document_ids == ("doc-1", "doc-2")
    assert request.metadata_filters["labels"] == ("a", "b")
    with pytest.raises(ValidationError):
        make_request(document_ids=["doc-1", "doc-1"])
    with pytest.raises(ValidationError):
        make_request(metadata_filters={"user_id": "other-user"})
    with pytest.raises(ValidationError):
        make_request(metadata_filters={"metadata..bad": "x"})
    with pytest.raises(ValidationError):
        make_request(metadata_filters={"labels": []})


class FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.asyncio
async def test_knowledge_base_ownership_and_active_versions_are_resolved_per_user():
    session = FakeSession([
        ("doc-1", "version-1", "active", "indexed"),
        ("doc-2", "version-2", "queued", "queued"),
    ])
    preparer = RetrievalRequestPreparer()
    context = await preparer.prepare(
        make_request(document_ids=["doc-1"]),
        authenticated_user_id="user-a",
        session=session,
    )
    assert isinstance(context, RetrievalQueryContext)
    assert context.user_id == "user-a"
    assert context.knowledge_base_id == "kb-1"
    assert context.active_version_ids == ("version-1",)
    assert all("user-a" in statement.compile().params.values() for statement in session.statements)


@pytest.mark.asyncio
async def test_unknown_or_foreign_knowledge_base_and_documents_are_denied():
    resolver = RetrievalScopeResolver()
    request = RetrievalQueryProcessor().process(
        make_request(), authenticated_user_id="user-a"
    )
    with pytest.raises(RetrievalAccessDenied):
        await resolver.authorize(request, session=FakeSession([]))

    with pytest.raises(RetrievalAccessDenied):
        await resolver.authorize(
            RetrievalQueryProcessor().process(
                make_request(document_ids=["foreign-doc"]), authenticated_user_id="user-a"
            ),
            session=FakeSession([("doc-1", "version-1", "active", "indexed")]),
        )


def test_qdrant_filter_construction_always_constrains_user_kb_versions_and_status():
    context = RetrievalQueryContext(
        query="query",
        user_id="trusted-user",
        knowledge_base_id="kb-1",
        document_ids=("doc-1",),
        metadata_filters={"department": "Research", "tags": ("a", "b")},
        top_k=5,
        strategy=RetrievalStrategyName.DENSE,
        active_version_ids=("version-1", "version-2"),
    )
    point_filter = QdrantVectorStore.build_retrieval_filter(context)
    conditions = {condition.key: condition for condition in point_filter.must}
    assert conditions["user_id"].match.value == "trusted-user"
    assert conditions["knowledge_base_id"].match.value == "kb-1"
    assert conditions["index_status"].match.value == "active"
    assert conditions["document_version_id"].match.any == ["version-1", "version-2"]
    assert conditions["document_id"].match.any == ["doc-1"]
    assert conditions["metadata.department"].match.value == "Research"
    assert conditions["metadata.tags"].match.any == ["a", "b"]


def test_empty_authorized_version_scope_cannot_become_unfiltered_search():
    context = RetrievalQueryContext(
        query="query",
        user_id="user-1",
        knowledge_base_id="kb-1",
        top_k=5,
        strategy=RetrievalStrategyName.DENSE,
        active_version_ids=(),
    )
    with pytest.raises(EmptyRetrievalScope):
        QdrantVectorStore.build_retrieval_filter(context)
