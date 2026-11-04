# RAG Subsystem Architecture Assessment

**Status:** assessment only; no application implementation performed.  
**Scope:** integrate a production-oriented ingestion and retrieval subsystem with the current Knowledge AI backend.

## 1. Existing architecture summary

The repository is a Python 3.10+ FastAPI application. `app/main.py` creates the app, installs a lifespan hook for logging, includes routers, and exposes Prometheus metrics. API endpoints are under `app/api/v1/`, with router aggregation in `app/routers/`. Domain/application services live in `app/services/`; provider-facing code is under `app/infra/` (LLM, voice, notifications). Pydantic settings are centralized in `app/core/config.py`.

Persistence uses SQLAlchemy 2-style declarative models and Alembic. Runtime sessions are asynchronous; Alembic uses a synchronous PostgreSQL URL. Qdrant is already configured and used for per-user documents and chat history. RabbitMQ and Redis are integrated directly through service modules, but the current worker implementation is a custom asyncio/aio-pika worker, not Celery.

The repo currently contains `app/rag/` and some proposed `app/infra/` RAG adapter paths, but their modules are docstring-only placeholders. They do not represent implemented capabilities. The requested architecture must be implemented in stages and reconciled with the existing document memory service rather than assumed to be present.

## 2. Current relevant directory structure

```text
app/
  api/v1/                 FastAPI endpoint modules and dependencies
  core/config.py          Pydantic settings
  db/{base,session}.py    SQLAlchemy declarative base and async session
  infra/                  Existing LLM, voice, notification adapters;
                          placeholder vector_stores,
                          embeddings, rerankers directories
  models/                 User, auth, chat, support-chat SQLAlchemy models
  rag/                    Docstring-only proposed RAG package layout
  schemas/                Request/response Pydantic schemas
  services/               Auth, document, vector, chat, Redis, RabbitMQ services
workers/                  Custom ingestion and retry worker processes
  tasks/                  Docstring-only placeholder task module
alembic/versions/         Existing user/auth/chat/support-chat migrations
tests/                    Unit/API tests and tests/integration/
```

## 3. Existing components and missing components

| Area | Present | Missing for target |
|---|---|---|
| FastAPI | App factory-like module, lifespan, router registration, metrics | RAG routes and RAG dependency wiring |
| SQLAlchemy/Alembic | Async runtime session, sync migrations, models and migrations | Document, version, chunk, ingestion-job, and indexing-state entities |
| Authentication | JWT access tokens, refresh-token persistence, user/tenant claims | Explicit document permissions/sharing model and consistent authorization policy for RAG resources |
| Tenant identity | `User.tenant_id`; tenant-scoped uniqueness; tenant claims in tokens | A tenant/organization entity and tenant lifecycle/roles; tenant identity must remain trusted from auth context |
| Vector storage | Qdrant client in `UserVectorStore`; tenant/user filters | General-purpose vector-store port/adapter, dense model/provider metadata, robust upsert/delete/version semantics |
| Document storage | Uploaded bytes are parsed in-memory; text-like formats supported by `DocumentService` | Durable source file/object storage and content hash/version handling; PDFs and office documents currently rejected |
| Embeddings | `UserVectorStore` contains deterministic hash-based 256-dimensional vectors | Real embedding abstraction and provider adapters; provider/model/dimension configuration and migration strategy |
| Lexical search | None | Qdrant sparse vectors and sparse embedding configuration |
| Retrieval | Existing Qdrant similarity search for per-user memories | Semantic, lexical, hybrid RRF, filtering, reranking and context assembly services |
| Queue | RabbitMQ via `aio-pika`, custom worker and retry worker | Celery app, task registration, broker/result policy, idempotency and durable job state |
| Redis | Async Redis service | Defined Celery result backend / lock / dedup policy (Redis use should be decided, not duplicated) |
| Tests | pytest, asyncio, httpx ASGI client, service/API tests; integration marker and Testcontainers | RAG unit tests, adapter contract tests, tenant isolation tests, lifecycle/retry integration tests |

## 4. Proposed RAG architecture

Keep orchestration and domain contracts in `app/rag/`; put concrete external integrations in `app/infra/`. Treat Qdrant, embedding providers, Docling, and rerankers as adapters behind small interfaces. SQL should be the source of truth for document/job/index lifecycle and ownership; Qdrant should be a rebuildable derived index.

Suggested responsibility boundaries:

- `app/rag/ingestion/`: source validation, Docling loader, normalization, structural/token chunking, processor composition, pipeline orchestration.
- `app/rag/indexing/`: indexing orchestration, dense and sparse Qdrant writes, index generations, deletes, retries, synchronization and reindex operations.
- `app/rag/retrieval/`: query processing, independently callable semantic/lexical strategies, RRF fusion, optional reranking, filtering and context construction.
- `app/rag/embeddings/`: embedding service and provider selection policy.
- `app/rag/ports/`: protocols/interfaces for vector store, embeddings and reranker (and optionally source blob storage).
- `app/rag/models/`: domain types; SQLAlchemy persistence models should remain under `app/models/` to match current conventions.
- `app/infra/{vector_stores,embeddings,rerankers}/`: Qdrant and provider adapters.
- `workers/`: task process configuration and task entry points. Keep tasks thin: load job, invoke application services, record outcome.

Prefer adapters that expose tenant/document filters explicitly and require them at the interface boundary. Do not rely on collection names alone for tenant isolation. For Qdrant, use payload fields and mandatory tenant filters. Validate ownership in SQL/API before scheduling or mutating a resource.

## 5. Integration points with existing modules

- `app/main.py`, `app/routers/api.py`: register new RAG routers without changing the existing app startup model unnecessarily.
- `app/core/config.py`: add only missing, namespaced settings (Docling limits, embedding provider/model/dimensions, Celery broker/backend and retry policy). Existing `rabbitmq_url`, `redis_url`, `qdrant_url`, `openai_api_key`, and database settings should be reused.
- `app/db/session.py`, `app/db/base.py`: reuse `get_db`, async SQLAlchemy sessions, and declarative base.
- `app/models/__init__.py`, `alembic/env.py`: import new persistence models so Alembic metadata sees them; add forward-only migrations.
- `app/api/v1/dependencies.py`, `app/services/jwt_service.py`, `app/services/security.py`: use authenticated claims and existing dependency conventions; derive tenant/user IDs from auth context, never from untrusted body fields.
- `app/services/document_service.py`: current implementation handles text-like files, character chunking and writes each chunk to `UserVectorStore`; it is the key overlap/conflict. Decide whether it remains a separate chat-memory feature or is migrated behind a compatibility facade to the new durable RAG document service.
- `app/services/user_vector_store.py`: existing Qdrant memory index is not a general RAG index. Preserve its chat/history semantics; share low-level client/config only if the adapter contracts and payload schemas can be made explicit. Avoid silently changing vector dimensions or collection payload schema.
- `app/services/rabbitmq.py`, `workers/ingestion_worker.py`, `workers/retry_worker.py`, `config/queues.py`: current queue and worker path is aio-pika based. Celery would be a replacement/parallel worker framework, not a configuration already available. Plan an explicit migration or coexistence boundary to prevent duplicate consumers and duplicate ingestion.
- `tests/`: follow ASGI client, injected fake client/store, and marked integration conventions.

## 6. Proposed database entities

Use relational rows as lifecycle/source-of-truth records; keep full extracted text or large payloads in object storage where appropriate.

1. **Document** — `id`, `tenant_id`, optional `owner_user_id`, source filename/media type, storage URI, checksum, size, metadata, active version, status, created/updated/deleted timestamps. Add indexes beginning with `tenant_id`; define uniqueness/idempotency appropriate to tenant + source/checksum.
2. **DocumentVersion** — document FK, version number, checksum, parser/normalizer versions, source storage URI, extraction metadata, status and timestamps. Supports reproducibility and reprocessing.
3. **DocumentChunk** — document/version FK, tenant ID (for efficient scoped queries), ordinal, content or content URI, token count, structural path/page/coordinates, metadata, content hash. Unique `(version_id, ordinal)`; tenant-filtered index. Avoid relying on this SQL table as the high-volume search engine.
4. **IngestionJob** — document/version FK, tenant ID, status (`queued`, `processing`, `indexed`, `failed`, `cancelled`), attempt count, idempotency key, task ID, stage, retry-after, safe error code/message, timestamps. Use optimistic/lease semantics to recover abandoned jobs.
5. **IndexGeneration** (or per-document index state) — target/index schema version, embedding provider/model/dimension, dense/sparse index generation, status and timestamps. Needed for safe reindex/model changes and rollback/swap.

Tenant does not currently have a first-class table; `tenant_id` is a string identity on `User` and auth claims. Initially follow that established pattern and enforce authorization in every SQL query. Introducing a Tenant table/roles should be a separate product/domain decision, not an incidental RAG migration.

## 7. Proposed indexing lifecycle

1. Accept an upload or source reference; validate size/type and authorize tenant/owner.
2. Persist source and `Document`/`DocumentVersion`/`IngestionJob` transactionally. Use checksum/idempotency key to detect duplicate submissions.
3. Queue work using a transactional outbox if reliable DB-to-broker delivery is required; otherwise document the publish gap and add reconciliation. Do not hold a DB transaction open while parsing/indexing.
4. Worker claims a job with lease/attempt metadata and checks whether the version is already indexed.
5. Produce chunks and dense/sparse embeddings, then upsert both vector forms to Qdrant with stable IDs incorporating document version and chunk ordinal. Include tenant, document, version, permissions, and metadata fields.
6. Verify Qdrant accepted the expected chunk IDs/counts and vector forms; mark index generation/version active only after they succeed. Record partial state for recovery.
7. On update, index a new version/generation first, then atomically switch SQL active version and retire/delete old backend points. On deletion, tombstone in SQL first, enqueue idempotent backend deletes, and reconcile until removed.
8. Retry transient failures with bounded exponential backoff and jitter; classify permanent parse/validation errors; expose failed jobs and a manual retry/reindex operation. Add periodic reconciliation for expired leases and SQL/index drift.

## 8. Proposed ingestion lifecycle

1. API authenticates and derives `tenant_id` and `user_id`; validates MIME, extension, size, and upload policy.
2. Store original bytes durably; create document version and queued job.
3. Celery task receives only stable job/version IDs (not raw document bytes), loads authorized source from storage and records task correlation.
4. Docling parses supported document types in a bounded worker process. Enforce page, time, memory, and output limits; treat parsing as untrusted-input processing.
5. Normalize extracted representation while retaining structure: headings, page/section, tables, code, source offsets and metadata.
6. Chunk by structural boundaries first, then split oversized sections by tokenizer budget with overlap. Preserve deterministic IDs and provenance metadata.
7. Persist chunk metadata/state and hand chunks to indexing; stage-level statuses enable targeted retry without reparsing when safe.
8. Mark version complete only after configured indexes are verified and active; persist safe failure details and retry classification otherwise.

## 9. Proposed retrieval lifecycle

1. API authenticates, derives tenant/user context and validates requested document/collection filters against SQL permissions.
2. Normalize/process query; select embedding provider compatible with the target index generation.
3. Run dense and sparse Qdrant searches with mandatory tenant and active-version/permission filters, using separate candidate limits.
4. Deduplicate and fuse ranked lists with Reciprocal Rank Fusion; retain source ranks/scores for debugging and explainability.
5. Apply optional reranker, then enforce final count/token budget, deduplication and context ordering in context builder.
6. Return chunk content, citations/provenance and scores appropriate to API contract. Never let caller-supplied tenant filters broaden auth scope.

## 10. Dependency requirements

The current manifest already has FastAPI, SQLAlchemy async, Alembic, Qdrant client, aio-pika, Redis, LangChain integrations, and OpenAI/Google-related packages. Verify exact transitive APIs/versions from lockfiles when implementation begins.

Likely additions, only when the chosen design is approved:

- Docling distribution(s) and any selected format extras.
- Celery; choose whether RabbitMQ is broker and whether Redis is result backend, or avoid storing task results and keep job state in SQL.
- Tokenizer library compatible with the chosen embedding model (for example `tiktoken` only if model choice warrants it).
- Embedding provider packages only for providers not already covered by current integrations; Hugging Face may require a separate package/runtime footprint.
- Reranker package/client only after selecting local vs hosted reranking.
- Object storage SDK if source files are stored outside the application filesystem.

Avoid adding a second search database: dense and sparse retrieval should use Qdrant.

## 11. Conflicts and implementation risks

- **Queue framework conflict:** current ingestion/retry workers use aio-pika directly; target requires Celery. Running both on the same queues risks duplicate work and inconsistent retry semantics. Choose a migration or clearly partitioned queue/task boundary.
- **Two ingestion paths:** `DocumentService` performs synchronous in-memory parsing/chunking and currently rejects PDF/DOCX. New asynchronous Docling flow overlaps it. Keep chat-scoped memory distinct or provide a deliberate compatibility migration.
- **Vector semantics:** `UserVectorStore` makes deterministic hash vectors of fixed size 256, not semantic model embeddings. Its collection/index cannot be reused as if it were a production embedding index without rebuilding and versioning.
- **No durable file store:** async workers need a durable source reference; request-local bytes or process-local temp files are unsafe across worker processes/retries.
- **Tenant model is incomplete:** `tenant_id` exists, but no Tenant entity, tenant membership/role service, or resource ACL system is apparent. RAG must use existing trusted auth context and explicit ownership rules.
- **Cross-store consistency:** SQL and Qdrant cannot share one transaction. Outbox/reconciliation, idempotent writes and activation gates are required.
- **Embedding model changes:** changing provider/model/dimension requires separate collections/index generations and reindexing; config switches alone can corrupt query/index compatibility.
- **Docling resource/security footprint:** parsing complex/untrusted documents can consume CPU/memory and surface sensitive content in logs. Isolate, limit and sanitize worker failures.
- **Migration discovery:** models must be imported into metadata and Alembic env; avoid editing existing migrations. Database type and JSON portability should follow current PostgreSQL-focused patterns.
- **Placeholder layout may imply readiness:** many proposed files exist but contain only docstrings; architecture status and imports should not treat them as implemented services.
- **Deployment gap:** Compose and worker entry points need coordinated Celery worker and possibly object storage configuration, health checks and monitoring.

## 12. Recommended implementation sequence

1. Decide domain boundary: general tenant documents vs chat/user memory; define ownership, visibility, deletion, and API compatibility expectations.
2. Decide queue transition: replace aio-pika worker with Celery or formally partition workloads; specify broker, result, outbox, retry, and idempotency semantics.
3. Decide durable file storage and operational deployment for Docling workers, provider credentials, and resource limits.
4. Define domain contracts and lifecycle state machine; add SQL models and a new Alembic migration for document/version/job/index state.
5. Implement storage and tenant authorization APIs with ownership checks, then source persistence and upload/status endpoints.
6. Implement Docling loader, normalization, structure-aware/token-aware chunking, and deterministic provenance; validate locally with unit tests.
7. Implement embedding provider port and one selected provider first; persist model/dimension metadata.
8. Implement Qdrant dense and sparse upsert/search/delete with tenant filters and adapter contract tests.
9. Implement idempotent indexing orchestration, Celery tasks, retry classification, leases/outbox/reconciliation, and reindex activation.
10. Implement dense and sparse retrieval in Qdrant, RRF, optional reranking, and bounded context construction.
11. Add tenant-isolation/API tests, worker retry/recovery tests, adapter integration tests, migration checks, metrics and operational dashboards.
12. Migrate or explicitly retain existing `DocumentService` behavior; document deprecation/compatibility before changing callers.

## Inspection notes

This assessment was prepared from repository source, configuration, migrations, workers, dependency manifests and tests. No application code, dependencies, configuration, or migrations were changed. The existing RAG/infra placeholder package files were present at inspection time but are not functional implementations.
