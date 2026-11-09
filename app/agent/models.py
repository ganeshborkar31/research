"""Provider-neutral request, message, and result values for agent turns."""

from __future__ import annotations

import math
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.models.retrieval import RetrievalStrategyName

Identifier = Annotated[str, Field(min_length=1, max_length=255)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(ContractModel):
    role: MessageRole
    content: str
    name: str | None = Field(default=None, min_length=1, max_length=128)
    tool_call_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_tool_fields(self) -> "AgentMessage":
        if self.role == MessageRole.TOOL and (self.name is None or self.tool_call_id is None):
            raise ValueError("tool messages require name and tool_call_id")
        if self.role != MessageRole.TOOL and (self.name is not None or self.tool_call_id is not None):
            raise ValueError("name and tool_call_id are only valid for tool messages")
        return self


class ResponseConfig(ContractModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32_768, strict=True)

    @field_validator("temperature")
    @classmethod
    def finite_temperature(cls, value: float | None) -> float | None:
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("temperature must be finite")
        return value


class RetrievalConfig(ContractModel):
    document_ids: tuple[Identifier, ...] = ()
    top_k: int | None = Field(default=None, ge=1, strict=True)
    strategy: RetrievalStrategyName | None = None
    score_threshold: float | None = None

    @field_validator("document_ids")
    @classmethod
    def unique_documents(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 500 or len(set(value)) != len(value):
            raise ValueError("document_ids must be unique and contain at most 500 values")
        return value

    @field_validator("score_threshold")
    @classmethod
    def finite_threshold(cls, value: float | None) -> float | None:
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("score_threshold must be finite")
        return value


class AgentRequest(ContractModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: Identifier | None = None
    knowledge_base_id: Identifier | None = None
    response: ResponseConfig = Field(default_factory=ResponseConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    metadata: dict[str, str | int | float | bool] | None = None

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message cannot be blank")
        return value

    @model_validator(mode="after")
    def retrieval_requires_knowledge_base(self) -> "AgentRequest":
        if self.knowledge_base_id is None and self.retrieval != RetrievalConfig():
            raise ValueError("retrieval options require knowledge_base_id")
        return self


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class FinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    ERROR = "error"
    OTHER = "other"


class ErrorCategory(str, Enum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER = "provider"
    INTERNAL = "internal"


class AgentError(ContractModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=500)
    category: ErrorCategory
    retryable: bool
    request_reference: Identifier


class Citation(ContractModel):
    citation_number: int = Field(ge=1, strict=True)
    document_id: Identifier
    document_version_id: Identifier
    chunk_id: Identifier
    page_numbers: tuple[int, ...] = ()
    section_reference: tuple[str, ...] = ()
    source_ref: str | None = Field(default=None, max_length=2048)
    source_metadata: dict[str, object] = Field(default_factory=dict)
    relevance_score: float | None = None

    @field_validator("relevance_score")
    @classmethod
    def finite_relevance(cls, value: float | None) -> float | None:
        if value is not None and (isinstance(value, bool) or not math.isfinite(value)):
            raise ValueError("relevance_score must be finite")
        return value


class ToolExecution(ContractModel):
    tool_call_id: Identifier
    name: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern=r"^(succeeded|failed|denied|timed_out)$")
    duration_ms: int | None = Field(default=None, ge=0, strict=True)
    error_code: str | None = Field(default=None, max_length=100)


class TokenUsage(ContractModel):
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_tokens: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def consistent_total(self) -> "TokenUsage":
        if self.input_tokens is not None and self.output_tokens is not None:
            expected = self.input_tokens + self.output_tokens
            if self.total_tokens is not None and self.total_tokens != expected:
                raise ValueError("total_tokens must equal input_tokens + output_tokens")
        return self


class AgentResponse(ContractModel):
    request_id: Identifier
    conversation_id: Identifier
    message_id: Identifier | None = None
    status: ExecutionStatus
    assistant_output: str | None = None
    citations: tuple[Citation, ...] = ()
    tool_executions: tuple[ToolExecution, ...] = ()
    token_usage: TokenUsage | None = None
    model: str | None = Field(default=None, min_length=1, max_length=255)
    finish_reason: FinishReason | None = None
    error: AgentError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> "AgentResponse":
        if self.status == ExecutionStatus.COMPLETED:
            if self.assistant_output is None or self.error is not None:
                raise ValueError("completed responses require assistant_output and cannot contain error")
        elif self.error is None:
            raise ValueError("failed or incomplete responses require error information")
        if self.error is not None and self.error.request_reference != self.request_id:
            raise ValueError("error request_reference must match request_id")
        numbers = [citation.citation_number for citation in self.citations]
        if len(numbers) != len(set(numbers)):
            raise ValueError("citation_number values must be unique")
        return self
