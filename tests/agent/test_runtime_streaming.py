from types import SimpleNamespace

import pytest

from app.agent.llm.chat_model import ChatChunk, ChatProviderError, ChatResponse, TokenUsage
from app.agent.models import AgentRequest
from app.agent.runtime import (AgentRuntime, AuthenticatedUserContext, ExecutionContext,
                               ModelExecutor, RuntimeConfig, ToolExecutor)
from app.agent.tools import ToolRegistry


class Conversations:
    async def create(self, *_args, **_kwargs):
        return SimpleNamespace(id="conversation-1")

    async def get(self, *_args, **_kwargs):
        return SimpleNamespace(id="conversation-1")

    async def begin_turn(self, *_args, **_kwargs):
        return SimpleNamespace(user_message=SimpleNamespace(id="user-message-1"), already_started=False)

    async def list_messages(self, *_args, **_kwargs):
        return []

    async def complete_turn(self, *_args, **_kwargs):
        return SimpleNamespace(id="assistant-message-1")

    async def fail_turn(self, *_args, **_kwargs):
        pass


class StreamingModel:
    model = "fixture-model"

    async def generate(self, *_args, **_kwargs):
        return ChatResponse("Hello world", self.model, TokenUsage(2, 2, 4))

    async def stream(self, *_args, **_kwargs):
        yield ChatChunk("Hello ")
        yield ChatChunk("world", TokenUsage(2, 2, 4))


def make_runtime():
    model = StreamingModel()
    runtime = AgentRuntime(model_executor=ModelExecutor(model),
                           tool_executor=ToolExecutor(ToolRegistry()),
                           conversations=Conversations(),
                           config=RuntimeConfig(system_prompt="Be helpful"))
    return runtime


def make_context():
    return ExecutionContext(user=AuthenticatedUserContext("user-1"), session=object(),
                            request_id="request-1")


@pytest.mark.asyncio
async def test_stream_emits_ordered_deltas_and_canonical_final_response():
    runtime = make_runtime()
    request = AgentRequest(message="Hi")
    events = [event async for event in runtime.stream(request, make_context())]

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "request.started", "conversation.loaded", "message.started", "message.delta",
        "message.delta", "message.completed", "request.completed",
    ]
    assert "".join(event.data.text for event in events if event.event_type == "message.delta") == "Hello world"
    final = events[-1].data.response
    assert final.model_dump() == (await runtime.execute(request, make_context())).model_dump()
    assert events[-1].model_validate_json(events[-1].model_dump_json()) == events[-1]


@pytest.mark.asyncio
async def test_provider_failure_after_output_keeps_partial_response_and_one_terminal_event():
    class BrokenStreamingModel(StreamingModel):
        async def stream(self, *_args, **_kwargs):
            yield ChatChunk("partial ")
            raise ChatProviderError("sensitive provider detail")

    runtime = AgentRuntime(model_executor=ModelExecutor(BrokenStreamingModel()),
                           tool_executor=ToolExecutor(ToolRegistry()),
                           conversations=Conversations(),
                           config=RuntimeConfig(system_prompt="Be helpful"))
    events = [event async for event in runtime.stream(AgentRequest(message="Hi"), make_context())]

    terminal = [event for event in events if event.event_type in {"request.completed", "request.failed"}]
    assert len(terminal) == 1
    response = terminal[0].data.response
    assert terminal[0].event_type == "request.failed"
    assert response.status.value == "incomplete"
    assert response.assistant_output == "partial "
    assert "sensitive" not in response.error.message

