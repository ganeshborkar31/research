"""Agent tool adapter over the application's existing authorized RAG service."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.agent.models import Citation
from app.agent.tools import (
    AuthorizationRequirement,
    ToolRegistry,
    ToolAuthorizationError,
    ToolExecutionConfig,
    ToolExecutionContext,
)
from app.rag.models.retrieval import AssembledContext, RetrievalRequest, RetrievalResult
from app.rag.retrieval.query_processor import RetrievalAccessDenied
from app.rag.retrieval.service import RetrievalService


class RetrievalToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: str
    results: tuple[RetrievalResult, ...]
    context: AssembledContext
    citations: tuple[Citation, ...]


class RetrievalTool:
    name = "knowledge_base_retrieval"
    description = (
        "Search an authorized knowledge base using the configured dense, lexical, "
        "or hybrid retrieval strategy and return source-grounded context and citations."
    )
    input_schema = RetrievalRequest
    output_schema = RetrievalToolOutput
    authorization = AuthorizationRequirement.KNOWLEDGE_BASE_OWNER
    config = ToolExecutionConfig(enabled=True, timeout_seconds=30.0)

    def __init__(self, retrieval_service: RetrievalService) -> None:
        self.retrieval_service = retrieval_service

    async def authorize(self, context: ToolExecutionContext, arguments: RetrievalRequest) -> None:
        try:
            # This is the same preparer used by the retrieval service/API. It binds
            # the KB and any document filters to the trusted authenticated user.
            await self.retrieval_service.preparer.prepare(
                arguments,
                authenticated_user_id=context.authenticated_user_id or "",
                session=context.session,
            )
        except RetrievalAccessDenied as exc:
            raise ToolAuthorizationError("knowledge base is not accessible") from exc

    async def execute(
        self, context: ToolExecutionContext, arguments: RetrievalRequest
    ) -> RetrievalToolOutput:
        try:
            results, assembled = await self.retrieval_service.retrieve(
                arguments,
                authenticated_user_id=context.authenticated_user_id or "",
                session=context.session,
            )
        except RetrievalAccessDenied as exc:
            # Scope can change between the pre-execution authorization and query.
            raise ToolAuthorizationError("knowledge base is not accessible") from exc

        strategy = (arguments.strategy or self.retrieval_service.preparer.processor.config.default_strategy).value
        citations = tuple(_citation(index, result) for index, result in enumerate(results, start=1))
        return RetrievalToolOutput(
            strategy=strategy,
            results=tuple(results),
            context=assembled,
            citations=citations,
        )


def register_retrieval_tool(
    registry: ToolRegistry,
    *,
    retrieval_service: RetrievalService | None = None,
    config: ToolExecutionConfig | None = None,
) -> RetrievalTool:
    """Register the existing application retrieval service as an agent tool."""
    if retrieval_service is None:
        # Lazy import keeps the generic agent contracts independent from provider
        # and vector-store construction until this production tool is requested.
        from app.rag.retrieval.dependencies import get_retrieval_service

        retrieval_service = get_retrieval_service()
    tool = RetrievalTool(retrieval_service)
    registry.register(tool, config=config)
    return tool


def build_default_tool_registry(
    *, retrieval_service: RetrievalService | None = None,
    retrieval_config: ToolExecutionConfig | None = None,
) -> ToolRegistry:
    """Create the production registry with the existing RAG retrieval tool."""
    registry = ToolRegistry()
    register_retrieval_tool(registry, retrieval_service=retrieval_service, config=retrieval_config)
    return registry


def _citation(number: int, result: RetrievalResult) -> Citation:
    # Only expose normalized provenance and ranking fields. Retrieval metadata may
    # originate in arbitrary document payloads and is not a public API surface.
    metadata = {
        "score": result.score,
        "dense_score": result.dense_score,
        "lexical_score": result.lexical_score,
        "dense_rank": result.dense_rank,
        "lexical_rank": result.lexical_rank,
        "fused_score": result.fused_score,
        "rerank_rank": result.rerank_rank,
    }
    metadata = {key: value for key, value in metadata.items() if value is not None}
    return Citation(
        citation_number=number,
        document_id=result.document_id,
        document_version_id=result.document_version_id,
        chunk_id=result.chunk_id,
        page_numbers=result.page_numbers,
        section_reference=result.heading_path,
        source_ref=result.source_ref,
        source_metadata=metadata,
        relevance_score=result.score,
    )
