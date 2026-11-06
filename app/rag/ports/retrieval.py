"""Provider-neutral contracts between query preparation and retrieval strategies."""

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models.retrieval import RetrievalCandidate, RetrievalQueryContext, RetrievalRequest


class RetrievalRequestPreparerPort(Protocol):
    """Validate inputs, bind identity, and authorize KB/document ownership."""

    async def prepare(
        self,
        request: RetrievalRequest,
        *,
        authenticated_user_id: str,
        session: AsyncSession,
    ) -> RetrievalQueryContext: ...


class RetrievalStrategyPort(Protocol):
    """Accept only validated, authorized query context; execute no provider logic here."""

    async def retrieve(
        self, request: RetrievalQueryContext
    ) -> Sequence[RetrievalCandidate]: ...
