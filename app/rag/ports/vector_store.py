"""Port types for dense vector storage and similarity search."""

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from qdrant_client.http.models import Filter


@dataclass(frozen=True)
class VectorRecord:
    id: str
    vector: list[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorHit:
    id: str
    score: float
    payload: dict[str, Any]


class DenseSearchPort(Protocol):
    """Scoped search over an already-provisioned unnamed dense collection."""

    vector_size: int

    async def validate_vector_dimensions(self, expected_dimensions: int) -> None: ...

    async def search_filtered(
        self, vector: Sequence[float], *, limit: int, query_filter: Filter
    ) -> list[VectorHit]: ...
