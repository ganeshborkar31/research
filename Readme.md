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

Database configuration options:

- Local Docker Postgres: keep using `ASYNC_POSTGRES_URL` + `SYNC_POSTGRES_URL`.
- Cloud Postgres (AWS RDS, Supabase, Neon, etc.): set `DATABASE_URL` only.
- The app auto-derives async/sync SQLAlchemy URLs from `DATABASE_URL`.

Cloud examples:

```env
# AWS RDS (PostgreSQL)
DATABASE_URL=postgresql://db_user:db_pass@your-rds-endpoint:5432/your_db?sslmode=require
```

```env
# Supabase (Direct connection)
DATABASE_URL=postgresql://postgres:db_pass@db.xxxxx.supabase.co:5432/postgres?sslmode=require
```

```env
# Supabase (Session pooler)
DATABASE_URL=postgresql://postgres.xxxxx:db_pass@aws-0-ap-south-1.pooler.supabase.com:5432/postgres?sslmode=require
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

If you are using cloud DB (RDS/Supabase), you can run only app + infra services:

```bash
docker compose up -d --build api qdrant redis rabbitmq
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
docker compose run --rm --build migrate

# create a new migration file
docker compose run --rm --build migrate alembic revision --autogenerate -m "add users table"
```

## API Endpoints

- Health: <http://localhost:8000/health>
- Ready: <http://localhost:8000/ready>
- Docs: <http://localhost:8000/docs>
- Metrics: <http://localhost:8000/metrics>

## Support Agent

- Chat: `POST /api/v1/support/chat`
- Voice: `POST /api/v1/support/voice`
- Chat stream: `ws://localhost:8000/api/v1/support/chat/ws?token=<access_token>`
- Voice stream: `ws://localhost:8000/api/v1/support/voice/ws?token=<access_token>`

Notes:

- Requires a valid access token; `tenant_id` and `user_id` are derived from the JWT.
- Support chats are stored in the database; clients should send `chat_id` to continue a thread.
- Do not send `history` in the request; history is loaded from the database.
- Policies are loaded from `config/support_policies.json` if present.
- Sample policy file: `config/support_policies.sample.json`.
- Streaming endpoints use the MCP-style event schema (`input.text`, `input.audio`, `session.start`, `session.stop`).
- Send `chat_id` as a query param or via `session.start.session_id` to resume a support thread.

## Frontend (Next.js)

The frontend is isolated in `frontend/` and talks to the backend via HTTP + WebSocket.

Quick start:

```bash
cd frontend
npm install
npm run dev
```

Optional environment overrides:

```bash
# Defaults to http://localhost:8000
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000

# If not set, the app derives ws:// or wss:// from NEXT_PUBLIC_API_BASE_URL
NEXT_PUBLIC_WS_BASE_URL=ws://localhost:8000
```

Login options:

- Paste a JWT access token in the UI.
- Or use the built-in login form (`/api/v1/auth/login`) to fetch a token.

## SMTP OTP Setup

Use these `.env` variables for real email OTP dispatch:

```env
SMTP_HOST=smtp.yourprovider.com
SMTP_PORT=587
SMTP_USERNAME=your_user
SMTP_PASSWORD=your_password
SMTP_FROM_EMAIL=no-reply@yourdomain.com
SMTP_FROM_NAME=Knowledge AI
SMTP_STARTTLS=true
SMTP_USE_SSL=false
SMTP_REQUIRE_AUTH=true
SMTP_TIMEOUT_SECONDS=15
```

Behavior:

- `POST /api/v1/auth/signup` and `POST /api/v1/auth/request-otp` now return `503` if SMTP delivery fails.
- Mobile OTP dispatch is not configured yet and returns a clear error.

## Realtime Voice (MCP Phase 1)

- WebSocket endpoint: `ws://localhost:8000/api/v1/live-chat/voice/ws?token=<access_token>`
- Protocol: `mcp.voice.v1`
- Agent profiles endpoint: `GET /api/v1/live-chat/voice/agents`

Voice STT providers (backend `input.audio` transcription):

- `VOICE_STT_PROVIDER=auto` (default): Gemini if `GEMINI_API_KEY` exists, else Whisper if `WHISPER_API_KEY` exists, else stub.
- `VOICE_STT_PROVIDER=gemini`: force Gemini STT (`VOICE_STT_GEMINI_MODEL`, default `gemini-2.0-flash`).
- `VOICE_STT_PROVIDER=whisper`: force Whisper-compatible STT (`WHISPER_BASE_URL`, `WHISPER_MODEL`, `WHISPER_API_KEY`).
- `VOICE_STT_PROVIDER=stub`: deterministic local stub for tests.

Example `.env` for real backend audio transcription via Gemini:

```env
VOICE_STT_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
VOICE_STT_GEMINI_MODEL=gemini-2.0-flash
```

Voice TTS providers (backend `tts.chunk` playable audio):

- `VOICE_TTS_PROVIDER=auto` (default): ElevenLabs if `ELEVENLABS_API_KEY` exists, else OpenAI if `OPENAI_API_KEY` exists, else Azure if `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` exist, else stub.
- `VOICE_TTS_PROVIDER=elevenlabs`: `ELEVENLABS_API_KEY`, `VOICE_TTS_ELEVENLABS_VOICE_ID`, `VOICE_TTS_ELEVENLABS_MODEL_ID`, `VOICE_TTS_ELEVENLABS_OUTPUT_FORMAT`.
- `VOICE_TTS_PROVIDER=openai`: `OPENAI_API_KEY`, `VOICE_TTS_OPENAI_MODEL`, `VOICE_TTS_OPENAI_VOICE`, `VOICE_TTS_OPENAI_FORMAT`.
- `VOICE_TTS_PROVIDER=azure`: `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `VOICE_TTS_AZURE_VOICE_NAME`, `VOICE_TTS_AZURE_OUTPUT_FORMAT`.
- `VOICE_TTS_PROVIDER=stub`: deterministic local bytes for tests.

Example `.env` for real live voice output via ElevenLabs:

```env
VOICE_TTS_PROVIDER=elevenlabs
ELEVENLABS_API_KEY=your_key_here
VOICE_TTS_ELEVENLABS_VOICE_ID=EXAVITQu4vr4xnSDxMaL
VOICE_TTS_ELEVENLABS_MODEL_ID=eleven_turbo_v2_5
VOICE_TTS_ELEVENLABS_OUTPUT_FORMAT=mp3_44100_128
```

Client message examples:

```json
{"type":"session.start","session_id":"voice-session-1"}
```

```json
{"type":"agent.set","agent_id":"interview"}
```

## Twilio Voice + SMS Integration

New endpoints (backend):

- Incoming voice webhook: `POST /api/v1/twilio/voice/incoming`
- Voice turn handler webhook: `POST /api/v1/twilio/voice/respond`
- Incoming SMS webhook: `POST /api/v1/twilio/sms/incoming`
- Outbound SMS API (auth required): `POST /api/v1/twilio/sms/send`
- Outbound call API (auth required): `POST /api/v1/twilio/voice/call`

Required `.env`:

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+15551234567
```

Optional `.env`:

```env
# public base URL for Twilio callbacks (recommended in production)
TWILIO_PUBLIC_BASE_URL=https://your-public-domain.com
```

Twilio Console webhook setup:

- Voice webhook URL: `https://<your-domain>/api/v1/twilio/voice/incoming`
- SMS webhook URL: `https://<your-domain>/api/v1/twilio/sms/incoming`

Notes:

- Incoming voice uses TwiML `<Gather>` for interactive turn-by-turn speech.
- Incoming SMS and voice both use the existing AI voice chat runtime to generate replies.

```json
{"type":"input.text","text":"Tell me a quick interview tip"}
```

```json
{"type":"input.audio","mime_type":"audio/wav","audio_b64":"<base64-audio>"}
```

Server event examples:

```json
{"type":"session.started","protocol":"mcp.voice.v1","session_id":"voice-session-1"}
```

```json
{"type":"agent.updated","session_id":"voice-session-1","agent":{"agent_id":"interview","name":"Interview Coach","description":"...","domain":"career"}}
```

```json
{"type":"stt.final","session_id":"voice-session-1","text":"..."}
```

```json
{"type":"llm.chunk","session_id":"voice-session-1","content":"..."}
```

```json
{"type":"tts.chunk","session_id":"voice-session-1","mime_type":"audio/mpeg","audio_b64":"..."}
```

```json
{"type":"response.done","session_id":"voice-session-1","text":"..."}
```

Expected health response:

```json
{"status": "ok"}
```

Expected ready response:

```json
{"status": "ready"}
```

## Testing

- Unit/API tests (default, fast, no real infrastructure):

```bash
pytest -q
```

- Integration tests with Testcontainers (PostgreSQL + Redis + Qdrant):

```bash
pytest -q -m integration tests/integration
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
