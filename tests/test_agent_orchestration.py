import pytest

from app.agent.models import AgentRequest, ExecutionStatus
from app.agent.orchestration import AgentOrchestrator, AgentTurnContext
from app.agent.llm.chat_model import ChatResponse, TokenUsage


class FakeChatModel:
    model = "fixture-model"

    async def generate(self, messages, *, options=None):
        self.messages = messages
        self.options = options
        return ChatResponse("answer", self.model, TokenUsage(4, 2, 6))

    async def stream(self, messages, *, options=None):
        yield

    async def generate_structured(self, messages, schema, *, options=None):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_agent_graph_renders_context_and_normalizes_response():
    model = FakeChatModel()
    graph = AgentOrchestrator(model)
    result = await graph.run(
        AgentRequest(message="question", response={"temperature": 0.3, "max_output_tokens": 120}),
        AgentTurnContext(request_id="req-1", conversation_id="chat-1", system_prompt="Be helpful",
                         history=[{"role": "assistant", "content": "earlier"}], retrieval_context="source text"),
    )
    assert result.status == ExecutionStatus.COMPLETED
    assert result.assistant_output == "answer"
    assert result.token_usage.total_tokens == 6
    assert model.messages[0].role == "system"
    assert "source text" in model.messages[0].content
    assert model.options.temperature == 0.3
    assert model.options.max_tokens == 120


@pytest.mark.asyncio
async def test_agent_graph_returns_safe_provider_error():
    class Broken(FakeChatModel):
        async def generate(self, *_args, **_kwargs):
            raise TimeoutError("secret provider response")

    result = await AgentOrchestrator(Broken()).run(
        AgentRequest(message="question"),
        AgentTurnContext(request_id="req-2", conversation_id="chat-2", system_prompt="Be helpful"),
    )
    assert result.status == ExecutionStatus.FAILED
    assert result.error.category.value == "timeout"
    assert "secret" not in result.error.message

