############################
# Stage 1 — Builder
############################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Build deps (only here)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast installer)
RUN pip install --no-cache-dir uv

# Copy dependency file
COPY pyproject.toml ./

# Install deps into system site-packages
RUN uv pip install --system --no-cache .

############################
# Stage 2 — Runtime
############################
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Runtime-only deps
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Create non-root user (security)
RUN useradd -m appuser
USER appuser

# Copy application code
COPY app ./app

EXPOSE 8000

# Production server
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "1", \
     "--timeout", "60"]