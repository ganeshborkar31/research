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
from app.schemas.chat_api import (
    ChatCreateRequest,
    ChatMessageCreateRequest,
    ChatMessageOut,
    ChatReplyResponse,
    ChatSessionOut,
    ChatUpdateRequest,
)
from app.schemas.document_api import (
    ChatDocumentIngestResponse,
    ChatDocumentSearchHit,
    ChatDocumentSearchResponse,
    ChatDocumentTextUploadRequest,
)
from app.schemas.mcp_voice import MCPAgentSetIn, MCPClientEvent, MCPInputAudioIn, MCPInputTextIn, MCPSessionStartIn
from app.schemas.user_api import UserCreate, UserDeleteOut, UserOut, UserUpdate
from app.schemas.user_memory import UserDocumentIn, UserHistoryEventIn, UserMemoryHit
from app.schemas.voice_agent import VoiceAgentProfileOut

__all__ = [
    "SignupRequest",
    "LoginRequest",
    "RequestOTPRequest",
    "VerifyOTPRequest",
    "RefreshRequest",
    "AuthTokenResponse",
    "OTPRequestedResponse",
    "LogoutResponse",
    "ChatCreateRequest",
    "ChatUpdateRequest",
    "ChatSessionOut",
    "ChatMessageCreateRequest",
    "ChatMessageOut",
    "ChatReplyResponse",
    "ChatDocumentTextUploadRequest",
    "ChatDocumentIngestResponse",
    "ChatDocumentSearchHit",
    "ChatDocumentSearchResponse",
    "MCPClientEvent",
    "MCPSessionStartIn",
    "MCPAgentSetIn",
    "MCPInputTextIn",
    "MCPInputAudioIn",
    "VoiceAgentProfileOut",
    "UserCreate",
    "UserUpdate",
    "UserOut",
    "UserDeleteOut",
    "UserDocumentIn",
    "UserHistoryEventIn",
    "UserMemoryHit",
]
