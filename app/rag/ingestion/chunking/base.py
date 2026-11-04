"""Provider-independent chunking contracts and tokenizer adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from app.rag.models.chunk import ChunkReferences, DocumentChunk
from app.rag.models.document import NormalizedDocument


class ChunkingError(ValueError):
    """Base class for invalid normalized input and chunking configuration."""


class EmptyDocumentError(ChunkingError):
    """The normalized document contains no chunkable content."""


class MalformedDocumentError(ChunkingError):
    """The normalized document does not match the application contract."""


class Tokenizer(Protocol):
    def encode(self, text: str) -> list[int]: ...

    def decode(self, tokens: Sequence[int]) -> str: ...


class Chunker(Protocol):
    def chunk(self, document: NormalizedDocument, references: ChunkReferences) -> list[DocumentChunk]: ...


class TiktokenTokenizer:
    """Small tiktoken adapter selected by encoding, not embedding provider/model."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        try:
            import tiktoken
        except ImportError as exc:
            raise RuntimeError("tiktoken is required for token-based chunking") from exc
        self._encoding = tiktoken.get_encoding(encoding_name)

    def encode(self, text: str) -> list[int]:
        return self._encoding.encode(text, disallowed_special=())

    def decode(self, tokens: Sequence[int]) -> str:
        return self._encoding.decode(list(tokens))


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    chunk_size: int = 512
    overlap: int = 64
    tokenizer_encoding: str = "cl100k_base"

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if self.overlap < 0 or self.overlap >= self.chunk_size:
            raise ValueError("overlap must be non-negative and smaller than chunk_size")
        if not self.tokenizer_encoding.strip():
            raise ValueError("tokenizer_encoding must be non-empty")
