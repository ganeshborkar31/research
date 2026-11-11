import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from app.agent.models import Citation
from app.agent.retrieval_tool import (
    RetrievalTool,
    RetrievalToolOutput,
    build_default_tool_registry,
)
from app.agent.tools import (
    AuthorizationRequirement,
    ToolAuthorizationError,
    ToolExecutionConfig,
    ToolExecutionContext,
    ToolInputError,
    ToolRegistry,
    ToolUnavailableError,
)
from app.rag.models.retrieval import AssembledContext, RetrievalRequest, RetrievalResult, RetrievalStrategyName
from app.rag.retrieval.query_processor import RetrievalAccessDenied


class Input(BaseModel):
    value: int


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doubled: int


class ExampleTool:
    name = "example"
    description = "A test tool"
    input_schema = Input
    output_schema = Output
    authorization = AuthorizationRequirement.AUTHENTICATED_USER
    config = ToolExecutionConfig(timeout_seconds=1)

    def __init__(self, *, failure=None, delay=0):
        self.failure, self.delay, self.calls = failure, delay, 0

    async def authorize(self, context, arguments):
        return None

    async def execute(self, context, arguments):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        return {"doubled": arguments.value * 2}


@pytest.mark.asyncio
async def test_registry_registration_lookup_validation_and_normalized_execution():
    registry, tool = ToolRegistry(), ExampleTool()
    registry.register(tool)
    assert registry.lookup("example") is tool
    result = await registry.execute("example", {"value": 3},
                                    context=ToolExecutionContext(authenticated_user_id="u"),
                                    tool_call_id="call-1")
    assert result.output == {"doubled": 6}
    assert result.execution.status == "succeeded"
    assert result.execution.tool_call_id == "call-1"
    with pytest.raises(ToolInputError):
        await registry.execute("example", {"value": "invalid"},
                               context=ToolExecutionContext(authenticated_user_id="u"))
    with pytest.raises(ToolUnavailableError):
        registry.lookup("missing")
    with pytest.raises(ValueError):
        registry.register(tool)


@pytest.mark.asyncio
async def test_registry_authorization_availability_failures_and_timeout():
    registry, tool = ToolRegistry(), ExampleTool()
    registry.register(tool)
    with pytest.raises(ToolAuthorizationError):
        await registry.execute("example", {"value": 1}, context=ToolExecutionContext())
    assert tool.calls == 0
    registry.configure("example", enabled=False)
    assert registry.available_tools() == ()
    with pytest.raises(ToolUnavailableError):
        await registry.execute("example", {"value": 1},
                               context=ToolExecutionContext(authenticated_user_id="u"))

    failing = ToolRegistry()
    failing.register(ExampleTool(failure=RuntimeError("secret detail")))
    failure = await failing.execute("example", {"value": 1},
                                    context=ToolExecutionContext(authenticated_user_id="u"))
    assert failure.execution.status == "failed"
    assert failure.execution.error_code == "tool_execution_failed"

    slow = ToolRegistry()
    slow.register(ExampleTool(delay=0.05), config=ToolExecutionConfig(timeout_seconds=0.001))
    timeout = await slow.execute("example", {"value": 1},
                                 context=ToolExecutionContext(authenticated_user_id="u"))
    assert timeout.execution.status == "timed_out"


class FakePreparer:
    def __init__(self, *, denied=False):
        self.denied = denied
        self.users = []
        self.processor = SimpleNamespace(config=SimpleNamespace(default_strategy=RetrievalStrategyName.DENSE))

    async def prepare(self, request, *, authenticated_user_id, session):
        self.users.append(authenticated_user_id)
        if self.denied:
            raise RetrievalAccessDenied("no scope")
        return object()


class FakeRetrievalService:
    def __init__(self, *, denied=False):
        self.preparer = FakePreparer(denied=denied)
        self.retrieve_users = []
        self.requests = []
        self.denied = denied

    async def retrieve(self, request, *, authenticated_user_id, session):
        self.retrieve_users.append(authenticated_user_id)
        self.requests.append(request)
        if self.denied:
            raise RetrievalAccessDenied("no scope")
        result = RetrievalResult(
            document_id="doc", document_version_id="version", chunk_id="chunk",
            content="source text", score=0.9, page_numbers=(2,), heading_path=("Policy",),
            knowledge_base_id=request.knowledge_base_id,
            metadata={"origin": "handbook"}, source_ref="docs/policy.pdf",
            dense_score=0.9, dense_rank=1,
        )
        return [result], AssembledContext(chunks=(), text="source text", char_count=11, token_count=3)


@pytest.mark.asyncio
async def test_retrieval_adapter_uses_existing_service_identity_and_citations():
    service = FakeRetrievalService()
    tool = RetrievalTool(service)
    registry = ToolRegistry()
    registry.register(tool)
    context = ToolExecutionContext(authenticated_user_id="auth-user", session=object())
    payload = RetrievalRequest(query="policy", knowledge_base_id="kb", strategy="hybrid", rerank=True,
                               document_ids=("doc",), top_k=7)
    result = await registry.execute(tool.name, payload.model_dump(mode="json"), context=context)
    assert result.execution.status == "succeeded"
    assert service.preparer.users == ["auth-user"]
    assert service.retrieve_users == ["auth-user"]
    assert service.requests[0].strategy == RetrievalStrategyName.HYBRID
    assert service.requests[0].rerank is True
    assert service.requests[0].document_ids == ("doc",)
    output = RetrievalToolOutput.model_validate(result.output)
    assert output.context.text == "source text"
    assert output.citations == (Citation(
        citation_number=1, document_id="doc", document_version_id="version", chunk_id="chunk",
        page_numbers=(2,), section_reference=("Policy",), source_ref="docs/policy.pdf",
        source_metadata={"origin": "handbook", "knowledge_base_id": "kb", "score": 0.9,
                         "dense_score": 0.9, "lexical_score": None, "dense_rank": 1,
                         "lexical_rank": None, "fused_score": None, "rerank_rank": None},
        relevance_score=0.9,
    ),)


@pytest.mark.asyncio
async def test_retrieval_adapter_denies_foreign_scope_and_never_accepts_user_id_argument():
    denied_service = FakeRetrievalService(denied=True)
    tool = RetrievalTool(denied_service)
    registry = ToolRegistry()
    registry.register(tool)
    args = {"query": "policy", "knowledge_base_id": "foreign-kb", "strategy": "dense"}
    with pytest.raises(ToolAuthorizationError):
        await registry.execute(tool.name, args,
                               context=ToolExecutionContext(authenticated_user_id="user-a", session=object()))
    assert denied_service.retrieve_users == []
    with pytest.raises(ToolInputError):
        await registry.execute(tool.name, {**args, "user_id": "user-b"},
                               context=ToolExecutionContext(authenticated_user_id="user-a", session=object()))


def test_authenticated_context_comes_from_application_claims():
    context = ToolExecutionContext.from_authenticated_claims({"sub": " user-a "}, session="db")
    assert context.authenticated_user_id == "user-a"
    assert context.session == "db"
    with pytest.raises(ToolAuthorizationError):
        ToolExecutionContext.from_authenticated_claims({"sub": ""}, session="db")


def test_default_agent_registry_registers_existing_retrieval_service():
    service = FakeRetrievalService()
    registry = build_default_tool_registry(retrieval_service=service)
    assert registry.lookup("knowledge_base_retrieval").retrieval_service is service
    assert len(registry.available_tools()) == 1

