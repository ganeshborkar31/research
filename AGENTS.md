# Agent Guide

Instructions for coding agents working in this repository. Keep this file useful to both Codex and Claude; put task-specific instructions near the code they govern.

## Project overview

Knowledge AI is a FastAPI backend and Next.js frontend for a retrieval-augmented generation platform. The backend uses LangChain/LangGraph, PostgreSQL with Alembic, Qdrant, Redis, RabbitMQ, and optional external LLM, voice, and notification providers. Background workers live in `workers/`; the frontend lives in `frontend/`.

## Repository map

- `app/api/` and `app/routers/`: HTTP and WebSocket endpoints and router registration.
- `app/services/`: application and domain services; provider boundaries are under `app/services/ports/`.
- `app/infra/`: integrations with LLM, voice, and notification providers.
- `app/models/`, `app/schemas/`, `app/db/`: persistence models, API/data schemas, and database setup.
- `workers/`: ingestion and retry workers.
- `alembic/versions/`: ordered database migrations.
- `tests/`: unit/API tests; `tests/integration/` contains infrastructure-backed tests.
- `config/`: application configuration and sample policy data. Never put real credentials here.

## Working rules

- Read the relevant implementation and tests before changing behavior. Follow existing patterns and keep changes focused.
- Preserve API and WebSocket contracts unless the task explicitly calls for a contract change; update schemas, implementation, and tests together when they do change.
- Keep provider-specific code in `app/infra/` and depend on service ports where the project already defines them.
- Treat tenant and user identity as security boundaries. Derive trusted identity from authenticated context, validate ownership on reads and writes, and do not log tokens, credentials, or sensitive user content.
- Never read, print, commit, or copy secrets from `.env`. Use placeholders in examples. Do not weaken authentication or authorization to make a test pass.
- For database schema changes, add a new Alembic migration; do not edit an already applied migration. Check model and migration consistency.
- Keep integration tests marked `integration`; ordinary tests should not require live external services. Use stubs/fakes for provider behavior where practical.
- Avoid unrelated formatting, dependency, lockfile, and configuration changes. Do not modify user changes outside the task.
- Do not add new dependencies unless they are needed; update the appropriate manifest and lockfile when adding one.

## Useful commands

Install the project and test dependencies with `uv sync --extra test` or `pip install -e ".[test]"`.

```bash
# Fast unit/API suite (integration tests are excluded by pytest.ini)
pytest -q

# Infrastructure-backed tests (requires Docker)
pytest -q -m integration tests/integration

# Run the backend through Docker Compose
docker compose up -d --build

# Run the frontend
cd frontend && npm install && npm run dev
```

Use the narrower relevant test or check while iterating. Run broader checks when the task and environment support them. Do not claim a check passed unless it was run and its result was observed.

## Completion notes

Summarize the behavioral changes, list checks actually run, and call out any relevant limitation. Do not include secret values in output.
