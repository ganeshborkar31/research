import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.schemas.user_memory import UserMemoryHit
from app.services.jwt_service import create_access_token


class FakeChatService:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(id="chat-1", tenant_id="tenant-1", user_id="user-1")

    async def get_chat(self, db, *, chat_id: str, tenant_id: str, user_id: str):
        if chat_id != self.chat.id:
            return None
        if tenant_id != self.chat.tenant_id or user_id != self.chat.user_id:
            return None
        return self.chat


class FakeDocumentService:
    def __init__(self) -> None:
        self.ingest_text_calls = []
        self.ingest_file_calls = []
        self.search_calls = []

    async def ingest_text(self, **kwargs):
        self.ingest_text_calls.append(kwargs)
        return SimpleNamespace(
            document_id=kwargs.get("document_id") or "doc-auto",
            source=kwargs["source"],
            chunk_count=2,
            total_characters=len(kwargs["content"]),
            created_at=datetime.now(timezone.utc),
        )

    async def ingest_file(self, **kwargs):
        self.ingest_file_calls.append(kwargs)
        return SimpleNamespace(
            document_id=kwargs.get("document_id") or "file-doc",
            source=kwargs.get("source") or "uploaded_document",
            chunk_count=1,
            total_characters=32,
            created_at=datetime.now(timezone.utc),
        )

    async def search_documents(self, **kwargs):
        self.search_calls.append(kwargs)
        return [
            UserMemoryHit(
                point_id="pt-1",
                score=0.93,
                content="Private policy snippet",
                source="uploaded_document",
                created_at=datetime.now(timezone.utc),
                metadata={"filename": "policy.txt"},
            )
        ]


@pytest.mark.asyncio
async def test_chat_document_text_upload_endpoint(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings
    import app.api.v1.chats as chats_api
    from app.api.v1.chats import router as chats_router

    get_settings.cache_clear()
    fake_chat = FakeChatService()
    fake_docs = FakeDocumentService()
    monkeypatch.setattr(chats_api, "get_chat_service", lambda: fake_chat)
    monkeypatch.setattr(chats_api, "get_document_service", lambda: fake_docs)

    async def override_get_db():
        yield object()

    app = FastAPI()
    app.include_router(chats_router, prefix="/api/v1/chats")
    app.dependency_overrides[get_db] = override_get_db

    token = create_access_token("user-1", "tenant-1").token
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chats/chat-1/documents/text",
            headers=headers,
            json={
                "document_id": "doc-1",
                "content": "This is my private policy document",
                "source": "uploaded_document",
                "metadata": {"filename": "policy.txt"},
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["chat_id"] == "chat-1"
    assert payload["document_id"] == "doc-1"
    assert fake_docs.ingest_text_calls[0]["chat_id"] == "chat-1"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_chat_document_upload_file_and_search_endpoints(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings
    import app.api.v1.chats as chats_api
    from app.api.v1.chats import router as chats_router

    get_settings.cache_clear()
    fake_chat = FakeChatService()
    fake_docs = FakeDocumentService()
    monkeypatch.setattr(chats_api, "get_chat_service", lambda: fake_chat)
    monkeypatch.setattr(chats_api, "get_document_service", lambda: fake_docs)

    async def override_get_db():
        yield object()

    app = FastAPI()
    app.include_router(chats_router, prefix="/api/v1/chats")
    app.dependency_overrides[get_db] = override_get_db

    token = create_access_token("user-1", "tenant-1").token
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upload_response = await client.post(
            "/api/v1/chats/chat-1/documents/upload",
            headers=headers,
            data={
                "source": "uploaded_document",
                "metadata_json": json.dumps({"filename": "policy.txt"}),
                "document_id": "doc-upload-1",
            },
            files={"file": ("policy.txt", b"private policy body", "text/plain")},
        )
        search_response = await client.get(
            "/api/v1/chats/chat-1/documents/search",
            headers=headers,
            params={"query": "policy", "limit": 3, "include_global": "false"},
        )

    assert upload_response.status_code == 201
    assert fake_docs.ingest_file_calls[0]["filename"] == "policy.txt"
    assert fake_docs.ingest_file_calls[0]["chat_id"] == "chat-1"
    assert fake_docs.ingest_file_calls[0]["document_id"] == "doc-upload-1"

    assert search_response.status_code == 200
    hits = search_response.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["content"] == "Private policy snippet"
    assert fake_docs.search_calls[0]["include_global"] is False
    get_settings.cache_clear()
