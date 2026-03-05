from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatDocumentTextUploadRequest(BaseModel):
    document_id: str | None = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500000)
    source: str = Field(default="user_document", min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatDocumentIngestResponse(BaseModel):
    chat_id: str
    document_id: str
    source: str
    chunk_count: int = Field(ge=1)
    total_characters: int = Field(ge=1)
    created_at: datetime


class ChatDocumentSearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    point_id: str
    score: float
    content: str
    source: str | None = None
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatDocumentSearchResponse(BaseModel):
    chat_id: str
    query: str
    hits: list[ChatDocumentSearchHit]
