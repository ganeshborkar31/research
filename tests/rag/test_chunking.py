import re

import pytest

from app.rag.ingestion.chunking import (
    ChunkReferences,
    ChunkingConfig,
    EmptyDocumentError,
    MalformedDocumentError,
    StructuralChunker,
)
from app.rag.models.document import DocumentElement, NormalizedDocument


class RecordingTokenizer:
    """Deterministic whitespace tokenizer used to assert exact limits and overlap."""

    def __init__(self):
        self._words = {}

    def encode(self, text):
        tokens = re.findall(r"\S+", text)
        for token in tokens:
            if token not in self._words:
                self._words[token] = len(self._words) + 1
        return [self._words[token] for token in tokens]

    def decode(self, tokens):
        inverse = {value: key for key, value in self._words.items()}
        return " ".join(inverse[token] for token in tokens)


REFS = ChunkReferences("doc-1", "tenant-1", "org-1", "kb-1")


def make_chunker(size=8, overlap=2):
    return StructuralChunker(ChunkingConfig(size, overlap), tokenizer=RecordingTokenizer())


def test_chunks_respect_token_limit_and_carry_references():
    document = NormalizedDocument("", (DocumentElement("paragraph", "one two three four five six seven eight nine ten"),))
    chunks = make_chunker(5, 1).chunk(document, REFS)
    assert chunks
    assert all(chunk.token_count <= 5 for chunk in chunks)
    assert all((chunk.document_id, chunk.tenant_id, chunk.organization_id, chunk.knowledge_base_id) ==
               ("doc-1", "tenant-1", "org-1", "kb-1") for chunk in chunks)


def test_overlap_is_retained_between_adjacent_chunks():
    document = NormalizedDocument("", (DocumentElement("paragraph", "one two three four five six seven eight nine ten"),))
    chunks = make_chunker(5, 2).chunk(document, REFS)
    assert chunks[0].content.split()[-2:] == chunks[1].content.split()[:2]
    assert chunks[1].content.split()[2] == "six"


def test_heading_context_and_page_references_are_preserved():
    document = NormalizedDocument("", (
        DocumentElement("section_header", "Overview", level=1, page_number=1),
        DocumentElement("paragraph", "first paragraph", page_number=2),
        DocumentElement("section_header", "Details", level=2, page_number=3),
        DocumentElement("paragraph", "second paragraph", page_number=4),
    ))
    chunks = make_chunker().chunk(document, REFS)
    assert any(chunk.heading_path == ("Overview",) and chunk.page_numbers == (1, 2) for chunk in chunks)
    assert any(chunk.heading_path == ("Overview", "Details") and "Details" in chunk.content
               and {3, 4}.issubset(set(chunk.page_numbers)) for chunk in chunks)


def test_table_is_kept_whole_when_it_fits_and_split_by_rows_when_large():
    table = "| Name | Value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |"
    short = make_chunker(32, 0).chunk(
        NormalizedDocument("", (DocumentElement("table", table, page_number=5),)), REFS)
    assert len(short) == 1
    assert "| beta | 2 |" in short[0].content

    large_table = "| name | value |\n| --- | --- |\n" + "".join(
        f"| item{i} | {i} |\n" for i in range(20))
    chunks = make_chunker(18, 0).chunk(
        NormalizedDocument("", (DocumentElement("table", large_table, page_number=5),)), REFS)
    assert len(chunks) > 1
    assert all(chunk.token_count <= 18 for chunk in chunks)
    assert all(chunk.page_numbers == (5,) for chunk in chunks)


def test_chunk_references_are_stable_for_identical_inputs():
    document = NormalizedDocument("", (DocumentElement("paragraph", "same content remains same"),))
    first = make_chunker().chunk(document, REFS)
    second = make_chunker().chunk(document, REFS)
    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]


def test_empty_and_malformed_documents_are_explicit_errors():
    with pytest.raises(EmptyDocumentError):
        make_chunker().chunk(NormalizedDocument("", ()), REFS)
    with pytest.raises(MalformedDocumentError):
        make_chunker().chunk(NormalizedDocument("", ("not an element",)), REFS)


def test_large_document_is_split_with_bounded_chunks():
    content = " ".join(f"term{i}" for i in range(10_000))
    chunks = make_chunker(256, 16).chunk(
        NormalizedDocument("", (DocumentElement("paragraph", content, page_number=9),)), REFS)
    assert len(chunks) > 30
    assert all(chunk.token_count <= 256 for chunk in chunks)
    assert all(chunk.page_numbers == (9,) for chunk in chunks)
