from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatDocumentTextUploadRequest(BaseModel):
    source_ref: str = Field(min_length=1, max_length=512)
    document_id: str | None = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=500000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ChatDocumentIngestResponse(BaseModel):
    chat_id: str
    document_id: str
    document_version_id: str
    job_id: str
    status: str
    version_number: int = Field(ge=1)
    source_ref: str


class ChatDocumentSearchHit(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    point_id: str
    score: float
    content: str
    source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatDocumentSearchResponse(BaseModel):
    chat_id: str
    query: str
    hits: list[ChatDocumentSearchHit]
