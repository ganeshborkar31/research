from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.v1.auth import router as auth_router
from app.api.v1.chats import router as chats_router
from app.api.v1.live_chat import router as live_chat_router
from app.api.v1.support import router as support_router
from app.api.v1.twilio import router as twilio_router
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
        support_router,
        prefix="/api/v1/support",
        tags=["Support"],
    )
    app.include_router(
        twilio_router,
        prefix="/api/v1/twilio",
        tags=["Twilio"],
    )
    app.include_router(
        chats_router,
        prefix="/api/v1/chats",
        tags=["Chats"],
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
