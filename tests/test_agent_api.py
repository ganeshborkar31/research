from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.models import AgentEvent, AgentEventData, AgentRequest, AgentResponse, ExecutionStatus
from app.agent.persistence import ConversationStore
from app.agent.retrieval_tool import register_retrieval_tool
from app.agent.runtime import AgentOrchestrator, AgentRuntime, ModelExecutor, ToolExecutor
from app.agent.tools import ToolRegistry
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User
from app.services.jwt_service import create_access_token
from app.api.v1.agent import get_agent_orchestrator
from app.rag.retrieval.query_processor import RetrievalAccessDenied


class FakeRuntime:
    def __init__(self):
        self.last_context = None

    async def stream(self, payload: AgentRequest, context) -> AsyncIterator[AgentEvent]:
        self.last_context = context
        yield AgentEvent(event_id="event-1", request_id=context.request_id,
                         conversation_id="new-conversation", event_type="request.started",
                         sequence=1, data=AgentEventData(phase="started"))
        response = AgentResponse(request_id=context.request_id, conversation_id="new-conversation",
                                 status=ExecutionStatus.COMPLETED, assistant_output="Hello")
        yield AgentEvent(event_id="event-2", request_id=context.request_id,
                         conversation_id="new-conversation", event_type="request.completed",
                         sequence=2, data=AgentEventData(response=response))


class FakeOrchestrator(AgentOrchestrator):
    def __init__(self):
        self.runtime = FakeRuntime()
        self.last_context = None

    async def run(self, payload: AgentRequest, context):
        self.last_context = context
        return AgentResponse(request_id=context.request_id, conversation_id="new-conversation",
                             status=ExecutionStatus.COMPLETED, assistant_output="Hello")


class DeniedScopeService:
    class Preparer:
        async def prepare(self, request, *, authenticated_user_id, session):
            assert authenticated_user_id == "agent-user"
            raise RetrievalAccessDenied("foreign knowledge base")

    def __init__(self):
        self.preparer = self.Preparer()
        self.called = False

    async def retrieve(self, request, *, authenticated_user_id, session):
        self.called = True
        raise AssertionError("retrieval must not run for an unauthorized knowledge base")


class NeverCalledModel:
    model = "configured-model"

    async def generate(self, *args, **kwargs):
        raise AssertionError("model must not run after authorization failure")

    async def stream(self, *args, **kwargs):
        raise AssertionError("model must not run after authorization failure")
        yield


@pytest_asyncio.fixture
async def agent_api(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([
            User(id="agent-user", username="agent", email="agent@example.test", password_hash="x"),
            User(id="other-user", username="other", email="other@example.test", password_hash="x"),
        ])
        await db.commit()

    async def override_db():
        async with factory() as db:
            yield db

    fake = FakeOrchestrator()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_agent_orchestrator] = lambda: fake
    token = create_access_token("agent-user").token
    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, headers, fake, factory
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_agent_orchestrator, None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_turn_auth_validation_trace_and_canonical_serialization(agent_api):
    client, headers, fake, _ = agent_api
    assert (await client.post("/api/v1/agent/turn", json={"message": "Hi"})).status_code == 401

    invalid = await client.post("/api/v1/agent/turn", headers=headers,
                                json={"message": "Hi", "user_id": "other-user"})
    assert invalid.status_code == 422

    response = await client.post("/api/v1/agent/turn", headers={**headers, "X-Request-ID": "trace-123"},
                                 json={"message": "Hi"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-123"
    assert response.json()["status"] == "completed"
    assert fake.last_context.user.user_id == "agent-user"
    assert fake.last_context.request_id == "trace-123"


@pytest.mark.asyncio
async def test_agent_stream_is_fetch_compatible_typed_sse_and_uses_auth_identity(agent_api):
    client, headers, fake, _ = agent_api
    response = await client.post("/api/v1/agent/turn/stream", headers=headers,
                                 json={"message": "Hi"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: request.started" in response.text
    assert "event: request.completed" in response.text
    assert '"response":{"request_id"' in response.text
    assert fake.runtime.last_context.user.user_id == "agent-user"


@pytest.mark.asyncio
async def test_agent_conversation_ownership_history_and_soft_archive(agent_api):
    client, headers, _, factory = agent_api
    store = ConversationStore()
    async with factory() as db:
        owned = await store.create(db, user_id="agent-user")
        foreign = await store.create(db, user_id="other-user")
        turn = await store.begin_turn(db, user_id="agent-user", conversation_id=owned.id,
                                      content="hello", request_id="history-1")
        await store.complete_turn(db, user_id="agent-user", conversation_id=owned.id,
                                  user_message_id=turn.user_message.id, assistant_content="hi")

    own_response = await client.get(f"/api/v1/agent/conversations/{owned.id}", headers=headers)
    assert own_response.status_code == 200
    history = await client.get(f"/api/v1/agent/conversations/{owned.id}/history", headers=headers)
    assert [item["role"] for item in history.json()] == ["user", "assistant"]
    assert (await client.get(f"/api/v1/agent/conversations/{foreign.id}", headers=headers)).status_code == 404
    assert (await client.get("/api/v1/agent/conversations/missing/history", headers=headers)).status_code == 404

    archived = await client.delete(f"/api/v1/agent/conversations/{owned.id}", headers=headers)
    assert archived.status_code == 204
    assert (await client.get(f"/api/v1/agent/conversations/{owned.id}", headers=headers)).status_code == 404
    async with factory() as db:
        from app.models import ChatSession
        persisted = await db.get(ChatSession, owned.id)
        assert persisted.status == "archived"


@pytest.mark.asyncio
async def test_agent_turn_rejects_knowledge_base_outside_authenticated_scope(agent_api):
    client, headers, _, _ = agent_api
    service = DeniedScopeService()
    registry = ToolRegistry()
    register_retrieval_tool(registry, retrieval_service=service)
    runtime = AgentRuntime(model_executor=ModelExecutor(NeverCalledModel()),
                           tool_executor=ToolExecutor(registry), conversations=ConversationStore())
    app.dependency_overrides[get_agent_orchestrator] = lambda: AgentOrchestrator(runtime)
    response = await client.post("/api/v1/agent/turn", headers=headers, json={
        "message": "secret query", "knowledge_base_id": "foreign-kb"
    })
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "tool_unauthorized"
    assert "foreign knowledge base" not in response.text
    assert service.called is False
