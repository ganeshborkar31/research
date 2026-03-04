from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.live_chat import router as live_chat_router
from app.api.v1.users import router as users_router
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

app.include_router(
    live_chat_router,
    prefix="/api/v1/live-chat",
    tags=["Live Chat"],
)

app.include_router(
    auth_router,
    prefix="/api/v1/auth",
    tags=["Auth"],
)

app.include_router(
    users_router,
    prefix="/api/v1/users",
    tags=["Users"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)
