"""Provider port shared by query and document embedding workflows."""

from collections.abc import Sequence
from typing import Protocol


class EmbeddingProvider(Protocol):
    """An embedding model with compatible query and document vector spaces."""

    dimensions: int

    async def embed_query(self, text: str) -> Sequence[float]: ...

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    async def close(self) -> None: ...
