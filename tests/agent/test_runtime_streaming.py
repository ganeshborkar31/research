import asyncio
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


@pytest.mark.asyncio
async def test_duplicate_pending_request_does_not_invoke_model_again():
    class DuplicateConversations(Conversations):
        async def begin_turn(self, *_args, **_kwargs):
            return SimpleNamespace(user_message=SimpleNamespace(id="user-message-1"), already_started=True)

        async def list_messages(self, *_args, **_kwargs):
            return []

    class CountingModel(StreamingModel):
        def __init__(self):
            self.calls = 0

        async def generate(self, *_args, **_kwargs):
            self.calls += 1
            return await super().generate(*_args, **_kwargs)

    model = CountingModel()
    runtime = AgentRuntime(model_executor=ModelExecutor(model),
                           tool_executor=ToolExecutor(ToolRegistry()),
                           conversations=DuplicateConversations())
    response = await runtime.execute(AgentRequest(message="Hi"), make_context())

    assert response.status.value == "failed"
    assert response.error.code == "duplicate_execution"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_runtime_timeout_is_normalized_and_concurrent_requests_keep_state_isolated():
    class DelayedModel(StreamingModel):
        async def generate(self, messages, *, options=None):
            await asyncio.sleep(0.02)
            return ChatResponse(messages[-1].content, self.model, TokenUsage())

    model = DelayedModel()
    runtime = AgentRuntime(model_executor=ModelExecutor(model),
                           tool_executor=ToolExecutor(ToolRegistry()),
                           conversations=Conversations())
    first_context = make_context()
    second_context = ExecutionContext(
        user=AuthenticatedUserContext("user-2"), session=object(), request_id="request-2",
    )
    first, second = await asyncio.gather(
        runtime.execute(AgentRequest(message="first"), first_context),
        runtime.execute(AgentRequest(message="second"), second_context),
    )
    assert first.assistant_output == "first"
    assert second.assistant_output == "second"
    assert first.request_id == "request-1"
    assert second.request_id == "request-2"

    timed_runtime = AgentRuntime(
        model_executor=ModelExecutor(DelayedModel()),
        tool_executor=ToolExecutor(ToolRegistry()),
        conversations=Conversations(),
        config=RuntimeConfig(timeout_seconds=0.001),
    )
    timed = await timed_runtime.execute(AgentRequest(message="slow"), make_context())
    assert timed.status.value == "failed"
    assert timed.error.code == "execution_timeout"


@pytest.mark.asyncio
async def test_closing_stream_cancels_provider_generation():
    class CancellableModel(StreamingModel):
        def __init__(self):
            self.cancelled = False
            self.wait = asyncio.Event()

        async def stream(self, *_args, **_kwargs):
            yield ChatChunk("partial")
            try:
                await self.wait.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    model = CancellableModel()
    runtime = AgentRuntime(model_executor=ModelExecutor(model),
                           tool_executor=ToolExecutor(ToolRegistry()),
                           conversations=Conversations())
    events = runtime.stream(AgentRequest(message="Hi"), make_context())
    async for event in events:
        if event.event_type == "message.delta":
            await events.aclose()
            break

    assert model.cancelled

