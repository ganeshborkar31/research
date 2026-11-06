from app.rag.models.retrieval import RetrievalResult
from app.rag.retrieval.context_builder import ContextAssembler


def result(chunk_id, content, **overrides):
    values = dict(
        document_id="doc-1", document_version_id="version-1", chunk_id=chunk_id,
        content=content, score=0.9, page_numbers=(2,), heading_path=("Guide", "Setup"),
        knowledge_base_id="kb-1", source_ref="manual.pdf", metadata={"filename": "manual.pdf"},
    )
    values.update(overrides)
    return RetrievalResult(**values)


class WordCounter:
    def count(self, text):
        return len(text.split())


def test_context_keeps_rank_order_provenance_and_multiple_documents_and_deduplicates():
    context = ContextAssembler(WordCounter()).assemble([
        result("a", "Top ranked passage"),
        result("a", "Duplicate chunk id"),
        result("b", "  Top   ranked passage "),
        result("c", "Second source", document_id="doc-2", source_ref="other.pdf"),
    ])
    assert [chunk.chunk_id for chunk in context.chunks] == ["a", "c"]
    assert context.text == "Top ranked passage\n\nSecond source"
    assert context.chunks[0].page_numbers == (2,)
    assert context.chunks[0].heading_path == ("Guide", "Setup")
    assert context.chunks[0].source_ref == "manual.pdf"
    assert context.chunks[1].document_id == "doc-2"


def test_context_respects_character_and_token_limits_and_truncates_oversized_chunk():
    assembler = ContextAssembler(WordCounter())
    chars = assembler.assemble([result("a", "abcdefghij"), result("b", "never")], max_chars=4)
    assert chars.text == "abcd"
    tokens = assembler.assemble([result("a", "one two three four")], max_tokens=2)
    assert tokens.text == "one two"
    assert tokens.token_count == 2
