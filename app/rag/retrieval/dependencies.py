"""FastAPI dependency wiring for authenticated retrieval query preparation."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_access_claims
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.rag.models.retrieval import RetrievalQueryContext, RetrievalRequest, RetrievalStrategyName
from app.rag.retrieval.query_processor import (
    QueryProcessingConfig,
    RetrievalAccessDenied,
    RetrievalQueryError,
    RetrievalQueryProcessor,
    RetrievalRequestPreparer,
)
from app.rag.dependencies import build_embedding_service
from app.rag.stores.qdrant import QdrantVectorStore
from app.rag.retrieval.semantic import DenseRetriever
from app.rag.retrieval.lexical import LexicalRetriever
from app.rag.retrieval.hybrid import HybridRetriever
from app.rag.retrieval.service import RetrievalService


def build_retrieval_request_preparer(
    settings: Settings | None = None,
) -> RetrievalRequestPreparer:
    settings = settings or get_settings()
    return RetrievalRequestPreparer(
        processor=RetrievalQueryProcessor(
            QueryProcessingConfig(
                default_top_k=settings.rag_retrieval_default_top_k,
                max_top_k=settings.rag_retrieval_max_top_k,
                default_score_threshold=settings.rag_retrieval_default_score_threshold,
                default_strategy=RetrievalStrategyName(settings.rag_retrieval_default_strategy),
            )
        )
    )


def build_dense_retriever(settings: Settings | None = None) -> DenseRetriever:
    """Wire the shared embedding factory to the existing configured Qdrant store."""
    settings = settings or get_settings()
    return DenseRetriever(
        embeddings=build_embedding_service(settings),
        vector_store=QdrantVectorStore(
            collection_name=settings.qdrant_collection_name,
            vector_size=settings.qdrant_vector_size,
        ),
    )


@lru_cache(maxsize=1)
def _get_retrieval_service() -> RetrievalService:
    dense = build_dense_retriever()
    lexical = LexicalRetriever()
    return RetrievalService(
        preparer=build_retrieval_request_preparer(),
        strategies={
            RetrievalStrategyName.DENSE: dense,
            RetrievalStrategyName.LEXICAL: lexical,
            RetrievalStrategyName.HYBRID: HybridRetriever(dense=dense, lexical=lexical),
        },
    )


def get_retrieval_service() -> RetrievalService:
    return _get_retrieval_service()


@lru_cache(maxsize=1)
def _get_preparer() -> RetrievalRequestPreparer:
    return build_retrieval_request_preparer()


async def get_retrieval_query_context(
    payload: Annotated[RetrievalRequest, Body()],
    claims: Annotated[dict, Depends(get_current_access_claims)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RetrievalQueryContext:
    """Authenticate, validate, authorize KB/document scope, and return strategy input."""
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user identity is missing.",
        )
    try:
        return await _get_preparer().prepare(
            payload, authenticated_user_id=user_id, session=session
        )
    except RetrievalAccessDenied as exc:
        # Use the same response for unknown KBs and foreign documents to avoid
        # exposing whether another user's identifiers exist.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found.") from exc
    except RetrievalQueryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
