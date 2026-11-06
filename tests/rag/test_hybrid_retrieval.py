import pytest

from app.rag.models.retrieval import RetrievalQueryContext, RetrievalResult, RetrievalStrategyName
from app.rag.retrieval.fusion import ResultFusion
from app.rag.retrieval.hybrid import HybridRetrievalConfig, HybridRetrievalError, HybridRetriever
from app.rag.retrieval.reranking import (
    CandidateReranker, RerankerRegistry, RerankingConfig, RerankingError,
)


def context():
    return RetrievalQueryContext(
        query="needle", user_id="user-a", knowledge_base_id="kb-a",
        document_ids=(), metadata_filters={}, top_k=3, score_threshold=None,
        strategy=RetrievalStrategyName.HYBRID, active_version_ids=("version-a",),
    )


def result(chunk, score, *, kb="kb-a", version="version-a"):
    return RetrievalResult(
        document_id="doc-a", document_version_id=version, chunk_id=chunk,
        content=chunk, score=score, knowledge_base_id=kb,
    )


class Strategy:
    def __init__(self, results=(), error=None):
        self.results, self.error, self.context = list(results), error, None

    async def retrieve(self, request):
        self.context = request
        if self.error:
            raise self.error
        return self.results


class Provider:
    def __init__(self, ids=None, error=None):
        self.ids, self.error = ids, error

    async def rerank(self, query, candidates, *, top_k):
        if self.error:
            raise self.error
        return self.ids


@pytest.mark.asyncio
async def test_combines_deduplicates_keeps_scores_ranks_and_independent_top_k():
    dense = Strategy([result("shared", .91), result("dense-only", .83)])
    lexical = Strategy([result("shared", 12), result("lexical-only", 11)])
    service = HybridRetriever(dense=dense, lexical=lexical,
                              config=HybridRetrievalConfig(7, 9, 5))
    output = await service.retrieve(context())
    assert dense.context.top_k == 7 and dense.context.strategy == RetrievalStrategyName.DENSE
    assert lexical.context.top_k == 9 and lexical.context.strategy == RetrievalStrategyName.LEXICAL
    assert [item.chunk_id for item in output][0] == "shared"
    shared = output[0]
    assert shared.dense_score == .91 and shared.lexical_score == 12
    assert shared.dense_rank == shared.lexical_rank == 1
    assert shared.score == shared.fused_score
    assert len(output) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("dense,lexical,expected", [
    ([result("d", .9)], [], ["d"]),
    ([], [result("l", 8)], ["l"]),
    ([], [], []),
])
async def test_handles_single_strategy_or_empty_results(dense, lexical, expected):
    output = await HybridRetriever(dense=Strategy(dense), lexical=Strategy(lexical)).retrieve(context())
    assert [item.chunk_id for item in output] == expected


@pytest.mark.asyncio
async def test_partial_failure_uses_successful_strategy_and_both_failures_are_explicit():
    retriever = HybridRetriever(dense=Strategy([result("d", 1)]),
                               lexical=Strategy(error=RuntimeError("offline")))
    assert [r.chunk_id for r in await retriever.retrieve(context())] == ["d"]
    with pytest.raises(HybridRetrievalError):
        await HybridRetriever(dense=Strategy(error=RuntimeError()),
                              lexical=Strategy(error=RuntimeError())).retrieve(context())


@pytest.mark.asyncio
async def test_rejects_results_outside_authorized_kb_and_versions():
    dense = Strategy([result("allowed", 1), result("foreign-kb", .99, kb="kb-b"),
                      result("foreign-version", .98, version="version-b")])
    output = await HybridRetriever(dense=dense, lexical=Strategy()).retrieve(context())
    assert [r.chunk_id for r in output] == ["allowed"]


@pytest.mark.asyncio
async def test_reranking_can_be_disabled_and_preserves_original_scores_and_fused_rank():
    item = result("a", 5).model_copy(update={"dense_score": .9, "dense_rank": 1, "fused_score": .02})
    disabled = CandidateReranker()
    assert (await disabled.rerank("q", [item]))[0].rerank_rank is None
    enabled = CandidateReranker(
        RerankingConfig(enabled=True, provider="fake", top_k=1),
        RerankerRegistry({"fake": Provider(["a"])}),
    )
    ranked = (await enabled.rerank("q", [item]))[0]
    assert ranked.rerank_rank == 1 and ranked.score == 5
    assert ranked.dense_score == .9 and ranked.dense_rank == 1 and ranked.fused_score == .02


@pytest.mark.asyncio
async def test_reranking_provider_failure_and_candidate_injection_are_explicit():
    failing = CandidateReranker(RerankingConfig(True, "fake", 2),
                                RerankerRegistry({"fake": Provider(error=RuntimeError())}))
    with pytest.raises(RerankingError):
        await failing.rerank("q", [result("a", 1)])
    injecting = CandidateReranker(RerankingConfig(True, "fake", 2),
                                  RerankerRegistry({"fake": Provider(["unauthorized"])}))
    with pytest.raises(RerankingError):
        await injecting.rerank("q", [result("a", 1)])


def test_rank_fusion_uses_ranks_instead_of_comparing_incomparable_scores():
    output = ResultFusion(rrf_constant=1).fuse(
        [result("dense-first", 100), result("both", 1)],
        [result("both", .01), result("lexical-second", .001)], top_k=5,
    )
    assert output[0].chunk_id == "both"
    assert output[0].dense_score == 1 and output[0].lexical_score == .01
