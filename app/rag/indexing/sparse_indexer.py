"""Lexical compatibility contract for the existing dense Qdrant collection.

The inspected collection has one unnamed 256-dimensional dense vector and no sparse
vector configuration. Document indexing therefore keeps searchable text and metadata
in payloads, without introducing a sparse-vector generator or claiming BM25 behavior.
"""
