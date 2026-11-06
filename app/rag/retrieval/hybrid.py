"""Parallel dense and lexical retrieval with rank fusion and optional reranking."""

import asyncio
import logging
from dataclasses import dataclass

from app.rag.models.retrieval import RetrievalQueryContext, RetrievalResult, RetrievalStrategyName
from app.rag.ports.retrieval import RetrievalStrategyPort
from app.rag.retrieval.fusion import ResultFusion
from app.rag.retrieval.reranking import CandidateReranker

logger = logging.getLogger(__name__)


class HybridRetrievalError(RuntimeError):
    """Both configured retrieval strategies failed."""


@dataclass(frozen=True)
class HybridRetrievalConfig:
    dense_top_k: int = 20
    lexical_top_k: int = 20
    result_top_k: int = 10
    allow_partial_results: bool = True

    def __post_init__(self) -> None:
        if min(self.dense_top_k, self.lexical_top_k, self.result_top_k) < 1:
            raise ValueError("hybrid top-k values must be positive")


class HybridRetriever:
    def __init__(self, *, dense: RetrievalStrategyPort, lexical: RetrievalStrategyPort,
                 config: HybridRetrievalConfig | None = None,
                 fusion: ResultFusion | None = None,
                 reranker: CandidateReranker | None = None) -> None:
        self.dense, self.lexical = dense, lexical
        self.config = config or HybridRetrievalConfig()
        self.fusion = fusion or ResultFusion()
        self.reranker = reranker or CandidateReranker()

    async def retrieve(self, context: RetrievalQueryContext) -> list[RetrievalResult]:
        if context.strategy != RetrievalStrategyName.HYBRID:
            raise ValueError("HybridRetriever requires the hybrid retrieval strategy")
        if not context.active_version_ids:
            return []
        dense_context = context.model_copy(update={"strategy": RetrievalStrategyName.DENSE,
                                                  "top_k": self.config.dense_top_k})
        lexical_context = context.model_copy(update={"strategy": RetrievalStrategyName.LEXICAL,
                                                    "top_k": self.config.lexical_top_k})
        outcomes = await asyncio.gather(self.dense.retrieve(dense_context),
                                        self.lexical.retrieve(lexical_context),
                                        return_exceptions=True)
        dense_results, lexical_results = outcomes
        failures = [item for item in outcomes if isinstance(item, BaseException)]
        if len(failures) == 2 or (failures and not self.config.allow_partial_results):
            raise HybridRetrievalError("Hybrid retrieval strategies failed") from failures[0]
        dense_results = [] if isinstance(dense_results, BaseException) else dense_results
        lexical_results = [] if isinstance(lexical_results, BaseException) else lexical_results
        # Strategy adapters enforce the scope at their provider boundary. Repeat
        # the version and KB checks here so an adapter regression cannot widen it.
        dense_results = [r for r in dense_results if self._authorized(r, context)]
        lexical_results = [r for r in lexical_results if self._authorized(r, context)]
        if failures:
            logger.warning("Hybrid retrieval returned partial results (%d strategy failure(s))", len(failures))
        fused = self.fusion.fuse(dense_results, lexical_results, top_k=self.config.result_top_k)
        return await self.reranker.rerank(context.query, fused)

    @staticmethod
    def _authorized(result: RetrievalResult, context: RetrievalQueryContext) -> bool:
        return (result.knowledge_base_id == context.knowledge_base_id
                and result.document_version_id in context.active_version_ids)
