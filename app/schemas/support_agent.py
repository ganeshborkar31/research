from __future__ import annotations

from pydantic import BaseModel, Field


class SupportChatRequest(BaseModel):
    chat_id: str | None = None
    message: str = Field(min_length=1)
    kb_user_id: str | None = None


class SupportChatResponse(BaseModel):
    chat_id: str
    reply: str
    policy_blocked: bool = False
    retrieval_context: str | None = None
    tool_outputs: dict | None = None


class SupportVoiceRequest(BaseModel):
    chat_id: str | None = None
    audio_b64: str | None = None
    text: str | None = None
    mime_type: str = "audio/wav"
    return_audio: bool = False
    kb_user_id: str | None = None


class SupportVoiceResponse(BaseModel):
    chat_id: str
    text: str
    audio_b64: str | None = None
    mime_type: str | None = None
