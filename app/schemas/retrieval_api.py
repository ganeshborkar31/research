"""Validated public request and response schemas for retrieval."""

from pydantic import BaseModel, ConfigDict

from app.rag.models.retrieval import AssembledContext, RetrievalRequest, RetrievalResult


class RetrievalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: str
    results: tuple[RetrievalResult, ...]
    context: AssembledContext
