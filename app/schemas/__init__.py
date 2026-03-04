"""Pydantic schemas and API contracts."""

from app.schemas.auth_api import (
    AuthTokenResponse,
    LoginRequest,
    LogoutResponse,
    OTPRequestedResponse,
    RefreshRequest,
    RequestOTPRequest,
    SignupRequest,
    VerifyOTPRequest,
)
from app.schemas.live_chat import LiveChatRequest, LiveChatResponse
from app.schemas.user_api import UserCreate, UserDeleteOut, UserOut, UserUpdate
from app.schemas.user_memory import UserDocumentIn, UserHistoryEventIn, UserMemoryHit

__all__ = [
    "SignupRequest",
    "LoginRequest",
    "RequestOTPRequest",
    "VerifyOTPRequest",
    "RefreshRequest",
    "AuthTokenResponse",
    "OTPRequestedResponse",
    "LogoutResponse",
    "LiveChatRequest",
    "LiveChatResponse",
    "UserCreate",
    "UserUpdate",
    "UserOut",
    "UserDeleteOut",
    "UserDocumentIn",
    "UserHistoryEventIn",
    "UserMemoryHit",
]
