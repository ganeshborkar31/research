"""Provider-neutral contract for candidate reranking."""

from collections.abc import Sequence
from typing import Protocol

from app.rag.models.retrieval import RetrievalResult


class RerankerPort(Protocol):
    async def rerank(
        self, query: str, candidates: Sequence[RetrievalResult], *, top_k: int
    ) -> Sequence[str]:
        """Return ordered stable chunk IDs selected from the supplied candidates."""
