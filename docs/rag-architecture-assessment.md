# RAG Architecture Assessment

**Scope:** personal, individual-user document ingestion and retrieval.

## Current architecture

Knowledge AI is a FastAPI backend. SQLAlchemy and Alembic manage application data. Qdrant is the vector store. User authentication uses individual accounts; RAG documents and chat history must be scoped to the authenticated `user_id`.

The RAG package owns document loading, normalization, chunking, embeddings, indexing, and retrieval contracts. Provider-specific integrations remain behind interfaces. Qdrant is an existing derived index and must not be replaced or duplicated without an approved migration.

## Ownership and indexing

- Derive `user_id` from authenticated server context for API operations. Never accept an owner identity from an untrusted request body.
- Store document, version, chunk, and provenance references in Qdrant payloads. Scope reads, replacement, and cleanup by user, document, and version.
- Use stable point IDs and staged version activation. Keep the last valid active version available until a replacement is fully indexed and activated.
- Keep chunking independent of embedding providers and preserve headings, tables, page references, and source metadata.
- Keep source content and indexing lifecycle state out of unrelated user profile records.

## Retrieval and compatibility

Phase 3 retrieval belongs under `app/rag/retrieval/` and consumes the new ingestion payload and PostgreSQL lifecycle state. PostgreSQL `rag_documents.active_version_id` is authoritative; Qdrant `index_status` is a secondary guard. The query foundation validates requests, derives identity through the existing access-claims dependency, and resolves knowledge-base/document ownership. Dense retrieval now uses the shared embedding service and existing Qdrant collection with mandatory user, knowledge-base, active-version, and status filters. Lexical, hybrid, and orchestration paths remain unimplemented. The older chat/document search in `app/services/` remains experimental and is unchanged.

The new ingestion workflow embeds documents through the shared `EmbeddingService` and writes to `Settings.qdrant_collection_name` (default `rag_chunks_v1`) with the configured embedding dimensions (provider defaults currently imply 1536 for OpenAI). Legacy `UserVectorStore` uses a deterministic hash embedding and defaults to `user_documents_v1` at 256 dimensions. These are different compatibility paths. Do not reuse the legacy embedding function for new document queries. Deployment configuration and the actual existing collection must be checked together; source defaults do not prove live Qdrant configuration. The new ingestion indexer validates an existing unnamed dense vector and never creates a collection.

There is no implemented lexical indexer: `app/rag/indexing/sparse_indexer.py` documents that the configured collection has no sparse vector and stores text in payloads. Do not describe this as BM25, add sparse vectors, or add a search service without a separate supported design decision. The detailed audit and proposed contracts are in [RAG retrieval architecture](rag-retrieval-architecture.md).

## Ingestion security and operations

Validate uploaded file size and detected file type, not only the extension. Treat files as untrusted data: do not execute content, prevent path traversal, and apply parser time and resource limits. Handle malformed, empty, partially extracted, and OCR-failed documents with explicit errors. Keep ordinary tests independent of live infrastructure and mark integration tests accordingly.

## Implementation boundary

The current account model is individual-user focused. Keep authentication, document scope, chunk metadata, and indexing tied to the authenticated user's personal account.
