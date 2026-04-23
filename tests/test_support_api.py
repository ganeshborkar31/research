import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.v1.dependencies import get_current_access_claims
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User
from app.models.support_chat import SupportChatMessage, SupportChatSession


TENANT_ID = "tenant-test"
USER_ID = "user-test"


class _StubVectorStore:
    async def search_user_documents(self, **_kwargs):
        return []

    async def store_history_event(self, *_args, **_kwargs):
        return None


class _StubRuntime:
    async def generate_reply(self, **_kwargs):
        return "stub-reply"

    async def stream_reply(self, **_kwargs):
        yield "stub-reply"


@pytest_asyncio.fixture
async def db_sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            session.add(
                User(
                    id=USER_ID,
                    tenant_id=TENANT_ID,
                    username="supportuser",
                    password_hash="x",
                    email="support@example.com",
                    status="active",
                    is_verified=True,
                    personal_data={},
                    preferences={},
                    compliance_flags={},
                )
            )
            await session.commit()
        yield session_maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def support_client(db_sessionmaker, monkeypatch):
    from app.main import app
    from app.services import support_agent_service as sas

    async def override_get_db():
        async with db_sessionmaker() as session:
            yield session

    def override_claims():
        return {"tenant_id": TENANT_ID, "sub": USER_ID}

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_access_claims] = override_claims

    sas._support_agent_service = None
    service = sas.get_support_agent_service()
    service._runtime = _StubRuntime()
    service._vector_store = _StubVectorStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_support_chat_persists_messages(support_client, db_sessionmaker):
    response = await support_client.post(
        "/api/v1/support/chat",
        json={"message": "Hello support"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["chat_id"]
    assert payload["reply"]

    async with db_sessionmaker() as session:  # type: AsyncSession
        count_sessions = await session.scalar(
            select(func.count(SupportChatSession.id))
        )
        count_messages = await session.scalar(
            select(func.count(SupportChatMessage.id))
        )

    assert count_sessions == 1
    assert count_messages == 2


@pytest.mark.asyncio
async def test_support_chat_reuses_chat_id(support_client, db_sessionmaker):
    first = await support_client.post(
        "/api/v1/support/chat",
        json={"message": "First issue"},
    )
    chat_id = first.json()["chat_id"]

    second = await support_client.post(
        "/api/v1/support/chat",
        json={"message": "Follow up", "chat_id": chat_id},
    )
    assert second.status_code == 200
    assert second.json()["chat_id"] == chat_id

    async with db_sessionmaker() as session:  # type: AsyncSession
        count_sessions = await session.scalar(
            select(func.count(SupportChatSession.id))
        )
        count_messages = await session.scalar(
            select(func.count(SupportChatMessage.id))
        )

    assert count_sessions == 1
    assert count_messages == 4
