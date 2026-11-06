"""Construction of bounded, provider-neutral context from ranked chunks."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Protocol

from app.rag.models.retrieval import AssembledContext, AssembledContextChunk, RetrievalResult


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class TiktokenCounter:
    """Token counting independent of any LLM provider or model choice."""

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding(encoding_name)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

class ContextAssembler:
    """Deduplicate ranked results and fit them to character and optional token bounds."""

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        self.token_counter = token_counter

    def assemble(
        self,
        results: Sequence[RetrievalResult],
        *,
        max_chars: int = 20_000,
        max_tokens: int | None = None,
    ) -> AssembledContext:
        if max_chars < 1 or (max_tokens is not None and max_tokens < 1):
            raise ValueError("context limits must be positive")
        counter = self.token_counter
        if max_tokens is not None and counter is None:
            counter = TiktokenCounter()

        chunks: list[AssembledContextChunk] = []
        seen_ids: set[str] = set()
        seen_text: set[str] = set()
        used_chars = 0
        used_tokens = 0
        for result in results:
            normalized = re.sub(r"\s+", " ", result.content).strip().casefold()
            if result.chunk_id in seen_ids or (normalized and normalized in seen_text):
                continue
            seen_ids.add(result.chunk_id)
            if normalized:
                seen_text.add(normalized)
            content = result.content.strip()
            if not content:
                continue
            separator_size = 2 if chunks else 0
            char_budget = max_chars - used_chars - separator_size
            if char_budget <= 0:
                break
            if len(content) > char_budget:
                content = content[:char_budget].rstrip()
            if not content:
                break
            token_count = counter.count(content) if counter else max(1, (len(content) + 3) // 4)
            if max_tokens is not None:
                current_text = "\n\n".join(chunk.content for chunk in chunks)
                prefix = f"{current_text}\n\n" if current_text else ""
                if counter.count(current_text) >= max_tokens:
                    break
                if counter.count(prefix + content) > max_tokens:
                    # Binary search the longest prefix that fits, without relying on
                    # a provider-specific tokenizer decode operation.
                    low, high = 0, len(content)
                    while low < high:
                        mid = (low + high + 1) // 2
                        if counter.count(prefix + content[:mid]) <= max_tokens:
                            low = mid
                        else:
                            high = mid - 1
                    content = content[:low]
                    token_count = counter.count(content)
            content = content.rstrip()
            if not content:
                break
            chunk = AssembledContextChunk(
                document_id=result.document_id,
                document_version_id=result.document_version_id,
                chunk_id=result.chunk_id,
                content=content,
                score=result.score,
                page_numbers=result.page_numbers,
                heading_path=result.heading_path,
                source_ref=result.source_ref,
                metadata=result.metadata,
                token_count=token_count,
            )
            chunks.append(chunk)
            used_chars += len(content) + separator_size
            used_tokens += token_count
            if len(content) < len(result.content.strip()):
                break

        text = "\n\n".join(chunk.content for chunk in chunks)
        total_tokens = counter.count(text) if counter else used_tokens
        return AssembledContext(
            chunks=tuple(chunks), text=text, char_count=len(text), token_count=total_tokens
        )
