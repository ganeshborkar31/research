"""Authenticated retrieval API, independent of answer generation."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import get_current_access_claims
from app.core.config import get_settings
from app.db.session import get_db
from app.rag.models.retrieval import RetrievalRequest
from app.rag.retrieval.dependencies import get_retrieval_service
from app.rag.retrieval.query_processor import (
    RetrievalAccessDenied,
    RetrievalQueryError,
)
from app.rag.retrieval.reranking import RerankingError
from app.rag.retrieval.service import RetrievalService, RetrievalServiceError
from app.schemas.retrieval_api import RetrievalResponse

router = APIRouter()


@router.post("/", response_model=RetrievalResponse)
async def retrieve_context(
    payload: RetrievalRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
    service: RetrievalService = Depends(get_retrieval_service),
) -> RetrievalResponse:
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user identity is missing.")
    try:
        results, context = await service.retrieve(
            payload, authenticated_user_id=user_id, session=db
        )
    except RetrievalAccessDenied as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found.") from exc
    except RetrievalQueryError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RerankingError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Requested reranking is unavailable.") from exc
    except RetrievalServiceError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Retrieval is temporarily unavailable.") from exc
    strategy = (payload.strategy.value if payload.strategy else get_settings().rag_retrieval_default_strategy)
    return RetrievalResponse(strategy=strategy, results=tuple(results), context=context)
