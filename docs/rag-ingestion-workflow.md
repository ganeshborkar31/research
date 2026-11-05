# Asynchronous RAG ingestion

The independent RAG ingestion entry point is `RagIngestionWorkflow.submit` in `app/rag/ingestion/workflow.py`. It accepts a local source reference, authenticated `user_id`, knowledge-base ID, document ID, optional idempotency key, metadata, and an optional force-reindex flag. Source files must live under the configured `RAG_SOURCE_ROOT`, which must be mounted at the same path in the API and worker containers.

Submission commits the document, immutable version, and queued job to PostgreSQL before publishing the Celery task. If RabbitMQ is unavailable, the queued job remains durable and the periodic reconciler republishes it. The Celery task contains only the job ID; it claims a database lease, parses with Docling, normalizes, chunks, embeds using the configured embedding service, and stages deterministic Qdrant points.

Qdrant writes are idempotent by user, document, version, and chunk. PostgreSQL changes the active version only after the full Qdrant write and activation succeed. A failed replacement therefore leaves the previous PostgreSQL active version intact. Retrieval must treat PostgreSQL's `active_version_id` as authoritative when filtering Qdrant results; Qdrant's point status is not the source of truth. Old Qdrant points are removed after the PostgreSQL switch; cleanup intent is persisted and retried by reconciliation. Deletion first records a PostgreSQL tombstone, then removes each user's document-version points; interrupted deletion remains in `deleting` for reconciliation.

The worker is the single Celery app in `workers/celery_app.py`. It uses the existing RabbitMQ service and embeds Celery Beat in the worker process for recovery. It does not use Redis as a broker or result backend.

The Qdrant adapter validates that the configured collection already exists and that its unnamed dense vector dimension matches the embedding configuration. It never creates a collection. Configure an approved existing collection and matching embedding dimensions before dispatching production jobs.
