"""Provider-neutral chunk domain types."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkReferences:
    """Ownership and source identifiers carried unchanged into every chunk."""

    document_id: str
    tenant_id: str
    organization_id: str | None = None
    knowledge_base_id: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    id: str
    content: str
    token_count: int
    ordinal: int
    document_id: str
    tenant_id: str
    organization_id: str | None = None
    knowledge_base_id: str | None = None
    heading_path: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
