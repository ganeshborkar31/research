# PRODUCT ARCHITECTURE (Real, Scalable)

```
                   ┌────────────────────┐
                   │ External Sources   │
                   │ Arxiv, Blogs, APIs │
                   └─────────┬──────────┘
                             │
                 ┌───────────▼────────────┐
                 │ Ingestion Microservice │
                 └───────────┬────────────┘
                             │
        ┌──────────────┐     ▼      ┌──────────────┐
        │ Auth Service │ ──▶ Vector DB ◀── Internal Docs
        └──────────────┘              └──────────────┘
                             │
                     ┌───────▼────────┐
                     │ RAG Engine     │
                     │ (LangChain)    │
                     └───────┬────────┘
                             │
               ┌─────────────▼─────────────┐
               │ API Gateway (FastAPI)     │
               └─────────────┬─────────────┘
                             │
               ┌─────────────▼─────────────┐
               │ Web App / Client Apps     │
               └───────────────────────────┘


raw_data → clean_text → chunk → embed → store → index


Query → Rewrite → Retrieve → Rerank → Context Filter → Generate → Verify

```



# Knowledge AI – Local Integration Testing Guide

This document explains how to start the system and test each service individually.

---

# 🚀 1. Start All Services

Build and start containers:

```bash
docker compose up -d --build
```

Check running services:

```bash
docker compose ps
```

View logs (example: API):

```bash
docker compose logs -f api
```

---

# 🌐 2. API Testing (FastAPI)

### Health Check (Liveness)

```
http://localhost:8000/health
```

Expected:

```json
{"status": "ok"}
```

✔ Confirms API container is running.

---

### Readiness Check (Infrastructure Validation)

```
http://localhost:8000/ready
```

Expected:

```json
{"status": "ready"}
```

✔ Confirms connections to:
- Postgres
- Redis
- Qdrant
- RabbitMQ

---

### API Docs (Swagger)

```
http://localhost:8000/docs
```

✔ Interactive API testing interface.

---

### Metrics (Prometheus)

```
http://localhost:8000/metrics
```

✔ Exposes application metrics for monitoring.

---

# 🐘 3. PostgreSQL Testing

Connect inside container:

```bash
docker exec -it rag-postgres psql -U raguser -d ragdb
```

Run:

```sql
SELECT 1;
\du
\l
```

✔ Confirms database and user are properly configured.

---

# 🟥 4. Redis Testing

Connect to Redis CLI:

```bash
docker exec -it rag-redis redis-cli
```

Run:

```bash
PING
```

Expected:

```
PONG
```

✔ Confirms Redis is operational.

---

# 🔎 5. Qdrant Testing

Open in browser:

```
http://localhost:6333/collections
```

Expected:

```json
{"result":{"collections":[]}}
```

✔ Confirms Vector Database is running.

---

# 🐇 6. RabbitMQ Testing

RabbitMQ Management UI:

```
http://localhost:15672
```

Login:
- Username: `guest`
- Password: `guest`

✔ Check:
- Queues
- Connections
- Exchanges

---

# 📊 7. Prometheus

```
http://localhost:9090
```

Run query:

```
up
```

✔ Confirms metrics collection from services.

---

# 📈 8. Grafana

```
http://localhost:3000
```

Default Login:
- admin
- admin

✔ Used for dashboards and observability.

---

# 📝 9. Logs (Loki + Promtail)

Logs are automatically collected and can be viewed in Grafana via Loki datasource.

✔ Enables centralized logging for all containers.

---

# 🧪 10. Full Integration Test

1. Ensure all containers are running.
2. Open:

```
http://localhost:8000/ready
```

If response is:

```json
{"status":"ready"}
```

The entire infrastructure stack is successfully integrated.

---

# 🛑 Stop Services

```bash
docker compose down
```

Remove volumes (clean reset):

```bash
docker compose down -v
```

---

# 📦 Architecture Overview

Services running locally:

- FastAPI (API)
- PostgreSQL (Database)
- Redis (Cache)
- Qdrant (Vector DB)
- RabbitMQ (Message Broker)
- Prometheus (Metrics)
- Grafana (Visualization)
- Loki (Log Aggregation)

---

# 🧠 Testing Strategy

Order of validation:

1. Containers running
2. API health
3. Infrastructure readiness
4. Metrics
5. Logs
6. Queue connectivity

This ensures production-grade reliability.

---
