from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.live_chat import router as live_chat_router
from app.api.v1.users import router as users_router


def include_api_routers(app: FastAPI) -> None:
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

