"""Token-aware splitting helpers shared by structural chunkers."""

from __future__ import annotations

from collections.abc import Sequence

from app.rag.ingestion.chunking.base import Tokenizer


def split_tokens(tokens: Sequence[int], max_tokens: int, overlap: int) -> list[list[int]]:
    """Split token IDs into bounded windows with a stable token overlap."""
    if max_tokens <= 0 or overlap < 0 or overlap >= max_tokens:
        raise ValueError("max_tokens must be positive and overlap smaller than max_tokens")
    if not tokens:
        return []
    step = max_tokens - overlap
    return [list(tokens[start:start + max_tokens]) for start in range(0, len(tokens), step)]


def split_text_tokens(text: str, max_tokens: int, overlap: int, tokenizer: Tokenizer) -> list[str]:
    return [tokenizer.decode(window) for window in split_tokens(tokenizer.encode(text), max_tokens, overlap)]
