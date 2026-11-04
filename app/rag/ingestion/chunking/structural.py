"""Structure-aware chunking with hard token limits and source provenance."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.rag.ingestion.chunking.base import (
    ChunkingConfig,
    EmptyDocumentError,
    MalformedDocumentError,
    TiktokenTokenizer,
    Tokenizer,
)
from app.rag.ingestion.chunking.token_based import split_tokens
from app.rag.models.chunk import ChunkReferences, DocumentChunk
from app.rag.models.document import DocumentElement, NormalizedDocument

_HEADING_KINDS = {"title", "heading", "section_header", "sectionheader"}
_TABLE_KINDS = {"table", "table_item"}


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    page_number: int | None
    kind: str


class StructuralChunker:
    """Group related document structure within a strict, configurable token budget."""

    def __init__(self, config: ChunkingConfig | None = None, *, tokenizer: Tokenizer | None = None) -> None:
        self.config = config or ChunkingConfig()
        self.tokenizer = tokenizer or TiktokenTokenizer(self.config.tokenizer_encoding)

    def chunk(self, document: NormalizedDocument, references: ChunkReferences) -> list[DocumentChunk]:
        elements = self._validate(document)
        if not isinstance(references, ChunkReferences) or not references.document_id or not references.tenant_id:
            raise MalformedDocumentError("document_id and tenant_id references are required")

        headings: list[tuple[int, str, int | None]] = []
        active_headings: tuple[str, ...] = ()
        active_heading_pages: tuple[int, ...] = ()
        output: list[DocumentChunk] = []
        body: list[int] = []
        pages: list[int] = []
        carried_pages: list[int] = []
        kinds: list[str] = []

        def prefix() -> str:
            return "\n".join(active_headings) + ("\n" if active_headings else "")

        def fits(tokens: list[int]) -> bool:
            return len(self.tokenizer.encode(prefix() + self.tokenizer.decode(tokens))) <= self.config.chunk_size

        def clear_carry() -> None:
            nonlocal body, carried_pages
            body = []
            carried_pages = []

        def flush(*, retain_overlap: bool = True) -> None:
            nonlocal body, pages, carried_pages, kinds
            if not kinds:
                return
            content = prefix() + self.tokenizer.decode(body)
            token_count = len(self.tokenizer.encode(content))
            if token_count > self.config.chunk_size:
                raise MalformedDocumentError("Chunk exceeds configured token limit")
            ordinal = len(output)
            page_numbers = tuple(dict.fromkeys(active_heading_pages + tuple(carried_pages + pages)))
            output.append(self._make_chunk(
                content, token_count, ordinal, references, active_headings, page_numbers,
                tuple(dict.fromkeys(kinds)),
            ))
            overlap = min(self.config.overlap, len(body)) if retain_overlap else 0
            body = body[-overlap:] if overlap else []
            carried_pages = list(pages) if overlap else []
            pages = []
            kinds = []

        def append_unit(unit: _Unit) -> None:
            nonlocal body, carried_pages
            separator = "\n" if unit.kind in _TABLE_KINDS | {"list_item", "listitem"} else "\n\n"
            encoded = self.tokenizer.encode((separator if body else "") + unit.text)
            if not encoded:
                return

            # Keep paragraphs and ordinary tables whole when they fit a fresh chunk.
            if fits(body + encoded):
                body.extend(encoded)
                if unit.page_number is not None:
                    pages.append(unit.page_number)
                kinds.append(unit.kind)
                return
            if kinds:
                flush()
            if not fits(body + encoded):
                body = []
                carried_pages = []
            if fits(encoded):
                body.extend(encoded)
                if unit.page_number is not None:
                    pages.append(unit.page_number)
                kinds.append(unit.kind)
                return

            # The unit itself is larger than an empty chunk. Split only this oversized unit.
            position = 0
            while position < len(encoded):
                low, high = 1, len(encoded) - position
                best = 0
                while low <= high:
                    mid = (low + high) // 2
                    if fits(body + encoded[position:position + mid]):
                        best = mid
                        low = mid + 1
                    else:
                        high = mid - 1
                if best == 0:
                    if kinds:
                        flush()
                        continue
                    if body:
                        body = []  # Preserve overlap when possible; drop it only if it blocks progress.
                        carried_pages = []
                        continue
                    raise MalformedDocumentError("Heading context leaves no room for document content")
                body.extend(encoded[position:position + best])
                if unit.page_number is not None:
                    pages.append(unit.page_number)
                kinds.append(unit.kind)
                position += best
                if position < len(encoded):
                    flush()

        for element in elements:
            kind = element.kind.strip().lower()
            text = element.text.strip()
            if kind in _HEADING_KINDS:
                if not text:
                    continue
                level = element.level or 1
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, text, element.page_number))
                if kinds:
                    flush(retain_overlap=False)
                else:
                    clear_carry()
                active_headings = tuple(heading for _, heading, _ in headings)
                active_heading_pages = tuple(page for _, _, page in headings if page is not None)
                if len(self.tokenizer.encode(prefix())) >= self.config.chunk_size:
                    raise MalformedDocumentError("Heading context consumes the full chunk token budget")
                continue

            if not text:
                if kind not in {"picture", "image"}:
                    continue
                text = element.caption or element.image_ref or "[Image]"
            unit = _Unit(text, element.page_number, kind)
            max_unit_tokens = self.config.chunk_size - len(self.tokenizer.encode(prefix()))
            units = self._table_units(unit, max_unit_tokens) if kind in _TABLE_KINDS else [unit]
            for part in units:
                append_unit(part)

        flush(retain_overlap=False)
        if not output:
            raise EmptyDocumentError("Normalized document contains no chunkable text")
        return output

    def _table_units(self, unit: _Unit, max_tokens: int) -> list[_Unit]:
        encoded = self.tokenizer.encode(unit.text)
        if len(encoded) <= max_tokens:
            return [unit]
        rows = unit.text.splitlines()
        header: list[str] = []
        if len(rows) > 1 and re.fullmatch(r"\s*\|?\s*[:|\- ]+\s*\|?\s*", rows[1]):
            header, rows = rows[:2], rows[2:]
        chunks: list[str] = []
        current = "\n".join(header)
        for row in rows:
            candidate = f"{current}\n{row}" if current else row
            if len(self.tokenizer.encode(candidate)) <= max_tokens:
                current = candidate
                continue
            if current and current != "\n".join(header):
                chunks.append(current)
            if len(self.tokenizer.encode(row)) > max_tokens:
                row_tokens = self.tokenizer.encode(row)
                chunks.extend(self.tokenizer.decode(window) for window in split_tokens(row_tokens, max_tokens, 0))
                current = "\n".join(header)
            else:
                current = "\n".join(header + [row]) if header else row
        if current and current != "\n".join(header):
            chunks.append(current)
        return [_Unit(chunk, unit.page_number, unit.kind) for chunk in chunks] or [unit]

    @staticmethod
    def _validate(document: NormalizedDocument) -> tuple[DocumentElement, ...]:
        if not isinstance(document, NormalizedDocument):
            raise MalformedDocumentError("document must be a NormalizedDocument")
        if not isinstance(document.text, str) or not isinstance(document.elements, (tuple, list)):
            raise MalformedDocumentError("normalized document text/elements have invalid types")
        elements = tuple(document.elements)
        for element in elements:
            if not isinstance(element, DocumentElement):
                raise MalformedDocumentError("normalized document contains a non-DocumentElement value")
            if not isinstance(element.kind, str) or not isinstance(element.text, str):
                raise MalformedDocumentError("normalized element kind and text must be strings")
            if element.page_number is not None and (not isinstance(element.page_number, int) or element.page_number < 1):
                raise MalformedDocumentError("page numbers must be positive integers")
            if element.level is not None and (not isinstance(element.level, int) or element.level < 1):
                raise MalformedDocumentError("heading levels must be positive integers")
            if element.caption is not None and not isinstance(element.caption, str):
                raise MalformedDocumentError("element captions must be strings")
            if element.image_ref is not None and not isinstance(element.image_ref, str):
                raise MalformedDocumentError("image references must be strings")
        if not any(element.text.strip() or element.caption or element.image_ref for element in elements):
            if document.text.strip():
                return (DocumentElement(kind="paragraph", text=document.text),)
            raise EmptyDocumentError("Normalized document is empty")
        return elements

    @staticmethod
    def _make_chunk(content: str, token_count: int, ordinal: int, references: ChunkReferences,
                    heading_path: tuple[str, ...], pages: tuple[int, ...], kinds: tuple[str, ...]) -> DocumentChunk:
        material = json.dumps({
            "document_id": references.document_id, "tenant_id": references.tenant_id,
            "organization_id": references.organization_id, "knowledge_base_id": references.knowledge_base_id,
            "ordinal": ordinal, "content": content,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return DocumentChunk(
            id=hashlib.sha256(material.encode("utf-8")).hexdigest(), content=content,
            token_count=token_count, ordinal=ordinal, document_id=references.document_id,
            tenant_id=references.tenant_id, organization_id=references.organization_id,
            knowledge_base_id=references.knowledge_base_id, heading_path=heading_path,
            page_numbers=pages, metadata={"element_kinds": kinds},
        )
