from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class UserDocumentIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    document_id: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=20000)
    source: str = Field(default="user_document", min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserHistoryEventIn(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20000)
    role: str = Field(default="user", min_length=1, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UserMemoryHit(BaseModel):
    point_id: str
    score: float
    content: str
    source: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

