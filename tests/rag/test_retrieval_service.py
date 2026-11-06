from types import SimpleNamespace

import pytest

from app.rag.models.retrieval import RetrievalRequest, RetrievalResult, RetrievalStrategyName
from app.rag.retrieval.context_builder import ContextAssembler
from app.rag.retrieval.service import RetrievalService


class Preparer:
    async def prepare(self, request, *, authenticated_user_id, session):
        assert authenticated_user_id == "trusted-user"
        return SimpleNamespace(
            query=request.query, knowledge_base_id=request.knowledge_base_id,
            active_version_ids=("v1",), strategy=request.strategy or RetrievalStrategyName.DENSE,
            rerank=False, max_context_chars=100, max_context_tokens=None,
        )


class Strategy:
    async def retrieve(self, context):
        return [RetrievalResult(
            document_id="d1", document_version_id="v1", chunk_id="c1",
            content="authorized context", score=0.8, knowledge_base_id="kb1",
        )]


@pytest.mark.asyncio
async def test_service_prepares_authenticated_scope_runs_selected_strategy_and_assembles_context():
    service = RetrievalService(
        preparer=Preparer(), strategies={RetrievalStrategyName.DENSE: Strategy()},
        context_assembler=ContextAssembler(),
    )
    results, context = await service.retrieve(
        RetrievalRequest(query="query", knowledge_base_id="kb1"),
        authenticated_user_id="trusted-user", session=object(),
    )
    assert [item.chunk_id for item in results] == ["c1"]
    assert context.text == "authorized context"
