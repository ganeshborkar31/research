import json

import pytest
from pydantic import ValidationError

from app.agent.models import (
    AgentError,
    AgentMessage,
    AgentRequest,
    AgentResponse,
    Citation,
    ErrorCategory,
    ExecutionStatus,
    FinishReason,
    MessageRole,
    RetrievalConfig,
    TokenUsage,
)


def test_request_defaults_and_round_trip_exclude_identity():
    request = AgentRequest.model_validate({"message": "  Summarize this.  "})
    assert request.message == "Summarize this."
    assert request.conversation_id is None
    assert request.knowledge_base_id is None
    assert request.response.max_output_tokens is None
    assert request.retrieval == RetrievalConfig()

    restored = AgentRequest.model_validate_json(request.model_dump_json())
    assert restored == request
    with pytest.raises(ValidationError):
        AgentRequest.model_validate({"message": "hello", "user_id": "trusted?"})


@pytest.mark.parametrize(
    "payload",
    [
        {"message": "  "},
        {"message": "hello", "retrieval": {"top_k": 3}},
        {"message": "hello", "knowledge_base_id": "kb", "retrieval": {"top_k": 0}},
        {"message": "hello", "knowledge_base_id": "kb", "retrieval": {"document_ids": ["d", "d"]}},
    ],
)
def test_request_rejects_invalid_combinations(payload):
    with pytest.raises(ValidationError):
        AgentRequest.model_validate(payload)


def test_internal_message_role_and_tool_validation():
    assert AgentMessage(role=MessageRole.USER, content="Hi").role == MessageRole.USER
    with pytest.raises(ValidationError):
        AgentMessage(role=MessageRole.TOOL, content="result")
    tool = AgentMessage(role=MessageRole.TOOL, content="result", name="search", tool_call_id="call-1")
    assert AgentMessage.model_validate_json(tool.model_dump_json()) == tool


def test_response_serialization_citation_and_optional_values():
    response = AgentResponse(
        request_id="req-1",
        conversation_id="conv-1",
        message_id="msg-1",
        status=ExecutionStatus.COMPLETED,
        assistant_output="The answer [1].",
        citations=(Citation(
            citation_number=1,
            document_id="doc-1",
            document_version_id="version-2",
            chunk_id="chunk-3",
            page_numbers=(4,),
            section_reference=("Policy", "Retention"),
            source_metadata={"kind": "pdf"},
            relevance_score=0.91,
        ),),
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        finish_reason=FinishReason.STOP,
    )
    restored = AgentResponse.model_validate_json(response.model_dump_json())
    assert restored == response
    payload = json.loads(response.model_dump_json())
    assert payload["model"] is None
    assert payload["tool_executions"] == []
    assert payload["citations"][0]["document_version_id"] == "version-2"


def test_error_structure_and_response_invariants():
    error = AgentError(
        code="provider_timeout",
        message="The response could not be generated.",
        category=ErrorCategory.TIMEOUT,
        retryable=True,
        request_reference="req-2",
    )
    failed = AgentResponse(
        request_id="req-2",
        conversation_id="conv-1",
        status=ExecutionStatus.FAILED,
        error=error,
        finish_reason=FinishReason.ERROR,
    )
    assert AgentResponse.model_validate_json(failed.model_dump_json()) == failed
    with pytest.raises(ValidationError):
        AgentResponse(request_id="req", conversation_id="conv", status=ExecutionStatus.FAILED)
    with pytest.raises(ValidationError):
        AgentResponse(
            request_id="req", conversation_id="conv", status=ExecutionStatus.FAILED,
            error=error,
        )
