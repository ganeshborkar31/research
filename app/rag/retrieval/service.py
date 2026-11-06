"""Application service for authorized retrieval and context assembly."""

from collections.abc import Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models.retrieval import (
    AssembledContext,
    RetrievalQueryContext,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStrategyName,
)
from app.rag.ports.retrieval import RetrievalRequestPreparerPort, RetrievalStrategyPort
from app.rag.retrieval.context_builder import ContextAssembler
from app.rag.retrieval.query_processor import RetrievalQueryError
from app.rag.retrieval.reranking import CandidateReranker, RerankingError


class RetrievalServiceError(RuntimeError):
    """A retrieval strategy or context assembly operation failed."""


class RetrievalService:
    """Coordinate validated scope, a selected strategy, reranking, and context."""

    def __init__(
        self,
        *,
        preparer: RetrievalRequestPreparerPort,
        strategies: Mapping[RetrievalStrategyName, RetrievalStrategyPort],
        context_assembler: ContextAssembler | None = None,
        reranker: CandidateReranker | None = None,
    ) -> None:
        self.preparer = preparer
        self.strategies = dict(strategies)
        self.context_assembler = context_assembler or ContextAssembler()
        self.reranker = reranker

    async def retrieve(
        self,
        request: RetrievalRequest,
        *,
        authenticated_user_id: str,
        session: AsyncSession,
    ) -> tuple[list[RetrievalResult], AssembledContext]:
        context: RetrievalQueryContext = await self.preparer.prepare(
            request, authenticated_user_id=authenticated_user_id, session=session
        )
        strategy = self.strategies.get(context.strategy)
        if strategy is None:
            raise RetrievalQueryError(f"Retrieval strategy {context.strategy.value!r} is unavailable")
        try:
            results: Sequence[RetrievalResult] = await strategy.retrieve(context)
            # Defensive user and scope check at the orchestration boundary.
            results = [
                result for result in results
                if result.knowledge_base_id == context.knowledge_base_id
                and result.document_version_id in context.active_version_ids
            ]
            if context.rerank:
                if self.reranker is None or not self.reranker.config.enabled:
                    raise RerankingError("Reranking is not configured")
                results = await self.reranker.rerank(context.query, results)
            context_result = self.context_assembler.assemble(
                results,
                max_chars=context.max_context_chars,
                max_tokens=context.max_context_tokens,
            )
            return list(results), context_result
        except (RetrievalQueryError, RerankingError):
            raise
        except Exception as exc:
            raise RetrievalServiceError("Retrieval failed") from exc
