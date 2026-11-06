"""Provider-neutral validation and normalization of retrieval requests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_document import RagDocument, RagDocumentVersion
from app.rag.models.retrieval import (
    ProcessedRetrievalRequest,
    RetrievalRequest,
    RetrievalQueryContext,
    RetrievalStrategyName,
)


class RetrievalQueryError(ValueError):
    """A retrieval request or its trusted scope is invalid."""


class RetrievalAccessDenied(PermissionError):
    """The authenticated user cannot access the requested knowledge base or document."""


@dataclass(frozen=True, slots=True)
class QueryProcessingConfig:
    default_top_k: int = 5
    max_top_k: int = 50
    default_score_threshold: float | None = None
    default_strategy: RetrievalStrategyName = RetrievalStrategyName.DENSE

    def __post_init__(self) -> None:
        if (
            isinstance(self.default_top_k, bool)
            or isinstance(self.max_top_k, bool)
            or self.default_top_k < 1
            or self.max_top_k < 1
        ):
            raise ValueError("retrieval top-k settings must be positive")
        if self.default_top_k > self.max_top_k:
            raise ValueError("default_top_k cannot exceed max_top_k")
        if self.default_score_threshold is not None and (
            isinstance(self.default_score_threshold, bool)
            or not math.isfinite(self.default_score_threshold)
        ):
            raise ValueError("default score threshold must be finite")
        if not isinstance(self.default_strategy, RetrievalStrategyName):
            raise ValueError("default_strategy must be a supported RetrievalStrategyName")


class RetrievalQueryProcessor:
    """Validate request inputs and bind them to a server-authenticated user scope.

    The requested knowledge-base ID is a filter. The authenticated user ID is a
    separate required argument and is always included in strategy input, so request
    data cannot broaden or replace user isolation.
    """

    def __init__(self, config: QueryProcessingConfig | None = None) -> None:
        self.config = config or QueryProcessingConfig()

    def process(
        self,
        request: RetrievalRequest | Mapping[str, object],
        *,
        authenticated_user_id: str,
    ) -> ProcessedRetrievalRequest:
        if not isinstance(authenticated_user_id, str) or not authenticated_user_id.strip():
            raise RetrievalQueryError("authenticated_user_id is required")
        try:
            parsed = (
                request
                if isinstance(request, RetrievalRequest)
                else RetrievalRequest.model_validate(request)
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise RetrievalQueryError("Invalid retrieval request") from exc

        top_k = parsed.top_k if parsed.top_k is not None else self.config.default_top_k
        if top_k > self.config.max_top_k:
            raise RetrievalQueryError(
                f"top_k cannot exceed configured maximum {self.config.max_top_k}"
            )
        return ProcessedRetrievalRequest(
            query=parsed.query,
            user_id=authenticated_user_id.strip(),
            knowledge_base_id=parsed.knowledge_base_id,
            document_ids=parsed.document_ids,
            metadata_filters=parsed.metadata_filters,
            top_k=top_k,
            score_threshold=(
                parsed.score_threshold
                if parsed.score_threshold is not None
                else self.config.default_score_threshold
            ),
            strategy=parsed.strategy or self.config.default_strategy,
            rerank=parsed.rerank,
            max_context_chars=parsed.max_context_chars,
            max_context_tokens=parsed.max_context_tokens,
        )


class RetrievalScopeResolver:
    """Resolve a knowledge-base request to the user's currently active versions."""

    async def authorize(
        self,
        request: ProcessedRetrievalRequest,
        *,
        session: AsyncSession,
    ) -> RetrievalQueryContext:
        # The current schema has no independent knowledge-base table. Ownership is
        # established from user-owned rag_documents and their explicit KB relation.
        statement = (
            select(
                RagDocument.id,
                RagDocument.active_version_id,
                RagDocument.status,
                RagDocumentVersion.status,
            )
            .outerjoin(
                RagDocumentVersion,
                and_(
                    RagDocumentVersion.id == RagDocument.active_version_id,
                    RagDocumentVersion.document_id == RagDocument.id,
                    RagDocumentVersion.user_id == RagDocument.user_id,
                ),
            )
            .where(
                RagDocument.user_id == request.user_id,
                RagDocument.knowledge_base_id == request.knowledge_base_id,
            )
        )
        result = await session.execute(statement)
        rows = result.all()
        if not rows:
            raise RetrievalAccessDenied("Knowledge base is not accessible")

        owned_document_ids = {row[0] for row in rows}
        requested_document_ids = set(request.document_ids)
        if requested_document_ids and not requested_document_ids.issubset(owned_document_ids):
            raise RetrievalAccessDenied("One or more documents are not accessible")

        eligible_rows = [
            row for row in rows
            if row[2] == "active" and row[1] is not None and row[3] == "indexed"
            and (not requested_document_ids or row[0] in requested_document_ids)
        ]
        return RetrievalQueryContext(
            **request.model_dump(),
            active_version_ids=tuple(dict.fromkeys(row[1] for row in eligible_rows)),
        )


class RetrievalRequestPreparer:
    """Validate client inputs, bind trusted identity, and authorize KB/document scope."""

    def __init__(
        self,
        processor: RetrievalQueryProcessor | None = None,
        scope_resolver: RetrievalScopeResolver | None = None,
    ) -> None:
        self.processor = processor or RetrievalQueryProcessor()
        self.scope_resolver = scope_resolver or RetrievalScopeResolver()

    async def prepare(
        self,
        request: RetrievalRequest | Mapping[str, object],
        *,
        authenticated_user_id: str,
        session: AsyncSession,
    ) -> RetrievalQueryContext:
        processed = self.processor.process(
            request, authenticated_user_id=authenticated_user_id
        )
        return await self.scope_resolver.authorize(processed, session=session)
