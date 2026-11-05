"""Provider-neutral chunk domain types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkReferences:
    """Individual-user ownership and source identifiers for every chunk."""

    document_id: str
    user_id: str
    knowledge_base_id: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    content: str
    token_count: int
    ordinal: int
    document_id: str
    user_id: str
    knowledge_base_id: str | None = None
    heading_path: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
