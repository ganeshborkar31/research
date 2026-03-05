from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatCreateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)


class ChatUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    user_id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class ChatMessageCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chat_id: str
    tenant_id: str
    user_id: str
    role: str
    content: str
    created_at: datetime


class ChatReplyResponse(BaseModel):
    chat_id: str
    user_message: ChatMessageOut
    assistant_message: ChatMessageOut

