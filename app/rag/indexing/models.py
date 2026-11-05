"""Document identity and index lifecycle types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentIndexScope:
    knowledge_base_id: str
    document_id: str
    document_version_id: str
    user_id: str

    def __post_init__(self) -> None:
        for name in (
            "knowledge_base_id",
            "document_id", "document_version_id", "user_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required for document indexing")


@dataclass(frozen=True, slots=True)
class IndexingResult:
    document_id: str
    document_version_id: str
    indexed_chunks: int
    deleted_stale_chunks: int
    idempotent: bool
