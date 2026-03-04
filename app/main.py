from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.core.logging import setup_logging
from app.routers import include_api_routers
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

include_api_routers(app)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)
