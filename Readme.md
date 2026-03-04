# Knowledge AI

## Overview

Knowledge AI is a local, containerized RAG platform built with FastAPI,
LangChain, PostgreSQL, Qdrant, Redis, RabbitMQ, and observability tools.

### Architecture Flow

- Ingestion: raw data -> clean text -> chunk -> embed -> store -> index
- Query: rewrite -> retrieve -> rerank -> context filter -> generate -> verify

## Initial Setup

1. Install prerequisites:
   - Docker
   - Docker Compose v2
   - VS Code + Python extension (only for debug mode)
2. Create or update `.env` at the project root.
3. Build images:

```bash
docker compose build
```

Why this is needed:

- `.env` provides runtime config for API, DB, and migration containers.
- Building ensures all Python dependencies, including `debugpy`, are in the
  image.

## Run Services

### Normal Run

```bash
docker compose up -d --build
```

Useful commands:

```bash
docker compose ps
docker compose logs -f api
```

### Debug Run

```bash
docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
```

What changes in debug mode:

- Base file runs `uvicorn` on port `8000`.
- Debug override runs `debugpy` and exposes port `5678`.
- `--wait-for-client` pauses app startup until your debugger attaches.

Why split normal and debug compose files:

- Normal mode stays simple and fast.
- Debug mode is opt-in and does not affect regular runs.

## VS Code Debug Attach

Your attach config is in `.vscode/launch.json`.

Expected values:

- Host: `localhost`
- Port: `5678`
- Path mapping: `${workspaceFolder}` -> `/app`

Attach steps:

1. Start the debug compose command.
2. Run `Attach FastAPI (Docker)` in VS Code.
3. Call an API endpoint to hit breakpoints.

## Alembic Migrations

- Concept: Alembic manages PostgreSQL schema changes as versioned migration
  files.
- Why we use it: Keeps DB schema consistent across local/dev/prod environments.

How migrations run in this project:

- `migrate` service in `docker-compose.yml` executes `alembic upgrade head`.
- `api` waits for `migrate` to complete successfully before startup.

Useful migration commands:

```bash
# apply latest migrations
docker compose run --rm migrate

# create a new migration file
docker compose run --rm migrate alembic revision --autogenerate -m "add users table"
```

## API Endpoints

- Health: <http://localhost:8000/health>
- Ready: <http://localhost:8000/ready>
- Docs: <http://localhost:8000/docs>
- Metrics: <http://localhost:8000/metrics>

Expected health response:

```json
{"status": "ok"}
```

Expected ready response:

```json
{"status": "ready"}
```

## Infrastructure Checks

### PostgreSQL

- Relational system of record for structured application data.
- Why we use it: Stores durable data such as users, metadata, and jobs.

```bash
docker exec -it rag-postgres psql -U raguser -d ragdb
```

```sql
SELECT 1;
\du
\l
```

### PgAdmin

- Web UI for managing PostgreSQL databases.
- Why we use it: Easier schema/data inspection than terminal-only SQL.

- UI: <http://localhost:5051>
- Login email: value of `PGADMIN_EMAIL` in `.env`
- Login password: value of `PGADMIN_PASSWORD` in `.env`

Add server in PgAdmin:

1. Name: `rag-postgres`
2. Host: `postgres`
3. Port: `5432`
4. Username: value of `POSTGRES_USER`
5. Password: value of `POSTGRES_PASSWORD`

### Redis

- In-memory key-value store for low-latency operations.
- Why we use it: Supports caching and short-lived state to reduce DB load.

```bash
docker exec -it rag-redis redis-cli
```

```text
PING
PONG
```

### Qdrant

- Vector database optimized for similarity search.
- Why we use it: Stores embeddings and retrieves semantically relevant chunks.

- Collections API: <http://localhost:6333/collections>

Expected response:

```json
{"result":{"collections":[]}}
```

### RabbitMQ

- Concept: Message broker for async, decoupled processing.
- Why we use it: Handles background tasks without blocking API requests.

- Management UI: <http://localhost:15672>
- Default username: `guest`
- Default password: `guest`

### Prometheus

- Time-series metrics collection and querying system.
- Why we use it: Scrapes service metrics for performance and reliability checks.

- UI: <http://localhost:9090>
- Query: `up`

### Grafana

- Visualization layer for metrics and logs.
- Why we use it: Builds operational dashboards and troubleshooting views.

- UI: <http://localhost:3000>
- Default username: `admin`
- Default password: `admin`

### Loki

- Log aggregation backend with label-based indexing.
- Why we use it: Centralizes container logs for cross-service debugging.

### Promtail

- Log shipping agent for Loki.
- Why we use it: Reads container logs and forwards them to Loki.

### Logs

Loki and Promtail collect container logs for Grafana exploration.

## Full Integration Check

1. Ensure all containers are running.
2. Open <http://localhost:8000/ready>.
3. If response is `{"status":"ready"}`, integration is successful.

## Stop and Reset

Stop services:

```bash
docker compose down
```

Stop and remove volumes 🛑:

```bash
docker compose down -v
```
