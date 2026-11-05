# RAG Architecture Assessment

**Scope:** personal, individual-user document ingestion and retrieval.

## Current architecture

Knowledge AI is a FastAPI backend and Next.js frontend. SQLAlchemy and Alembic manage application data. Qdrant is the vector store. User authentication uses individual accounts; RAG documents and chat history must be scoped to the authenticated `user_id`.

The RAG package owns document loading, normalization, chunking, embeddings, indexing, and retrieval contracts. Provider-specific integrations remain behind interfaces. Qdrant is an existing derived index and must not be replaced or duplicated without an approved migration.

## Ownership and indexing

- Derive `user_id` from authenticated server context for API operations. Never accept an owner identity from an untrusted request body.
- Store document, version, chunk, and provenance references in Qdrant payloads. Scope reads, replacement, and cleanup by user, document, and version.
- Use stable point IDs and staged version activation. Keep the last valid active version available until a replacement is fully indexed and activated.
- Keep chunking independent of embedding providers and preserve headings, tables, page references, and source metadata.
- Keep source content and indexing lifecycle state out of unrelated user profile records.

## Retrieval and compatibility

Use the existing Qdrant collection and its configured dense vector. Preserve the embedding compatibility between indexing and query paths. Lexical or hybrid retrieval should only use an existing supported implementation; do not assume lexical search means BM25 or invent sparse vectors without a demonstrated requirement. Do not add another vector database or search service.

## Ingestion security and operations

Validate uploaded file size and detected file type, not only the extension. Treat files as untrusted data: do not execute content, prevent path traversal, and apply parser time and resource limits. Handle malformed, empty, partially extracted, and OCR-failed documents with explicit errors. Keep ordinary tests independent of live infrastructure and mark integration tests accordingly.

## Implementation boundary

The current account model is individual-user focused. Keep authentication, document scope, chunk metadata, and indexing tied to the authenticated user's personal account.
