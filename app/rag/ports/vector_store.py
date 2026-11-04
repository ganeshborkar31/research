"""Port types for dense vector storage and similarity search."""

from dataclasses import dataclass, field
from typing import Any


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
