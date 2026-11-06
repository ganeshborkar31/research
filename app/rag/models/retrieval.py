"""Provider-neutral retrieval request and strategy types."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalStrategyName(str, Enum):
    """Strategy identifiers; selecting one does not imply its implementation exists."""

    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID = "hybrid"


MetadataScalar: TypeAlias = str | int | float | bool
MetadataFilterValue: TypeAlias = MetadataScalar | tuple[MetadataScalar, ...]

_IDENTIFIER = Annotated[str, Field(min_length=1, max_length=255)]


class RetrievalRequest(BaseModel):
    """Untrusted, provider-neutral retrieval inputs. Identity is never supplied here."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=20_000)
    knowledge_base_id: _IDENTIFIER
    document_ids: tuple[_IDENTIFIER, ...] = ()
    # Keys are paths relative to the Qdrant payload's `metadata` object.
    # Values mean equality; tuples mean membership in any listed value.
    metadata_filters: dict[str, MetadataFilterValue] = Field(default_factory=dict)
    top_k: int | None = Field(default=None, ge=1, strict=True)
    score_threshold: float | None = Field(default=None, strict=True)
    strategy: RetrievalStrategyName | None = None
    rerank: bool = False
    max_context_chars: int = Field(default=20_000, ge=1, le=200_000, strict=True)
    max_context_tokens: int | None = Field(default=None, ge=1, le=100_000, strict=True)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        # Trimming only removes surrounding whitespace; preserve case, punctuation,
        # internal whitespace, and quoted text exactly as supplied.
        normalized = value.strip()
        if not normalized:
            raise ValueError("query cannot be blank")
        return normalized

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 500:
            raise ValueError("at most 500 document_ids may be supplied")
        if len(set(values)) != len(values):
            raise ValueError("document_ids must not contain duplicates")
        return values

    @field_validator("metadata_filters")
    @classmethod
    def validate_metadata_filters(
        cls, filters: dict[str, MetadataFilterValue]
    ) -> dict[str, MetadataFilterValue]:
        reserved = {
            "user_id", "tenant_id", "organization_id", "knowledge_base_id",
            "document_id", "document_version_id", "index_status", "record_type",
        }
        for path, value in filters.items():
            if not path or len(path) > 512 or any(not part.strip() for part in path.split(".")):
                raise ValueError("metadata filter keys must be valid dotted paths")
            if path.split(".", 1)[0] in reserved:
                raise ValueError("ownership and index fields cannot be supplied as metadata filters")
            values = value if isinstance(value, tuple) else (value,)
            if not values:
                raise ValueError(f"metadata filter {path!r} cannot have an empty value list")
            for item in values:
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("metadata filter numbers must be finite")
                if not isinstance(item, (str, int, float, bool)):
                    raise ValueError("metadata filters support scalar equality values only")
                if isinstance(item, str) and not item.strip():
                    raise ValueError("metadata filter string values cannot be blank")
        return filters

    @field_validator("score_threshold")
    @classmethod
    def validate_score_threshold(cls, value: float | None) -> float | None:
        # Score ranges vary by backend and distance metric, so only finite numeric
        # values are enforced here; strategy adapters interpret the threshold.
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("score_threshold must be a finite number")
        return value

class ProcessedRetrievalRequest(BaseModel):
    """Validated strategy input with mandatory server-derived user isolation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    user_id: _IDENTIFIER
    knowledge_base_id: _IDENTIFIER
    document_ids: tuple[_IDENTIFIER, ...] = ()
    metadata_filters: dict[str, MetadataFilterValue] = Field(default_factory=dict)
    top_k: int = Field(ge=1)
    score_threshold: float | None = None
    strategy: RetrievalStrategyName
    rerank: bool = False
    max_context_chars: int = 20_000
    max_context_tokens: int | None = None


class RetrievalQueryContext(ProcessedRetrievalRequest):
    """Strategy-ready query context backed by an authenticated database scope."""

    active_version_ids: tuple[_IDENTIFIER, ...]


class RetrievalCandidate(BaseModel):
    """Provider-neutral result shape shared by future retrieval strategies."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    point_id: str
    content: str
    score: float
    payload: dict[str, object] = Field(default_factory=dict)
    strategy: RetrievalStrategyName


class RetrievalResult(BaseModel):
    """Public dense result preserving document and chunk provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    document_version_id: str
    chunk_id: str
    content: str
    score: float
    page_numbers: tuple[int, ...] = ()
    heading_path: tuple[str, ...] = ()
    knowledge_base_id: str
    metadata: dict[str, object] = Field(default_factory=dict)
    # Hybrid retrieval provenance. `score` remains the strategy score for
    # single-strategy retrieval and becomes the fused score for hybrid results.
    dense_score: float | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float | None = None
    rerank_rank: int | None = None
    source_ref: str | None = None


class AssembledContextChunk(BaseModel):
    """A bounded source chunk included in assembled context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    document_version_id: str
    chunk_id: str
    content: str
    score: float
    page_numbers: tuple[int, ...] = ()
    heading_path: tuple[str, ...] = ()
    source_ref: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    token_count: int


class AssembledContext(BaseModel):
    """Provider-neutral context and provenance for downstream generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunks: tuple[AssembledContextChunk, ...]
    text: str
    char_count: int
    token_count: int
