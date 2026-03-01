from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.core.logging import setup_logging
from prometheus_fastapi_instrumentator import Instrumentator


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    print("🚀 Starting Knowledge AI")
    yield
    print("🛑 Shutting down Knowledge AI")


app = FastAPI(
    title="Knowledge AI",
    version="0.1.0",
    lifespan=lifespan,
)

# Health routes
app.include_router(
    health_router,
    prefix="/api/v1/health",
    tags=["Health"],
    )

# Prometheus metrics
Instrumentator().instrument(app).expose(app)
