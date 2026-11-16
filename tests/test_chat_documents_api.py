from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.rag.retrieval.dependencies import get_retrieval_service
from app.services.jwt_service import create_access_token


class FakeChatService:
    async def get_chat(self, db, *, chat_id: str, user_id: str):
        if chat_id == "chat-1" and user_id == "user-1":
            return SimpleNamespace(id=chat_id, user_id=user_id)
        return None


class FakeWorkflow:
    def __init__(self):
        self.submissions = []

    def submit(self, **kwargs):
        self.submissions.append(kwargs)
        return {
            "document_id": kwargs["document_id"] or "doc-created",
            "document_version_id": "version-1",
            "job_id": "job-1",
            "status": "queued",
            "version_number": 1,
        }

    async def close(self):
        return None


class FakeRetrievalService:
    def __init__(self):
        self.calls = []

    async def retrieve(self, request, *, authenticated_user_id, session):
        self.calls.append((request, authenticated_user_id))
        hit = SimpleNamespace(
            chunk_id="chunk-1", score=0.93, content="Private policy snippet",
            source_ref="policy.txt", metadata={"filename": "policy.txt"},
        )
        return [hit], None


@pytest.mark.asyncio
async def test_chat_document_text_upload_uses_durable_ingestion(monkeypatch, tmp_path):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RAG_SOURCE_ROOT", str(tmp_path))
    from app.core.config import get_settings
    import app.api.v1.chats as chats_api

    get_settings.cache_clear()
    fake_chat = FakeChatService()
    fake_workflow = FakeWorkflow()
    monkeypatch.setattr(chats_api, "get_chat_service", lambda: fake_chat)
    monkeypatch.setattr(chats_api, "build_ingestion_workflow", lambda: fake_workflow)

    async def override_get_db():
        yield object()

    app = FastAPI()
    app.include_router(chats_api.router, prefix="/api/v1/chats")
    app.dependency_overrides[get_db] = override_get_db
    token = create_access_token("user-1").token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/chats/chat-1/documents/text",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "source_ref": "policy.txt",
                "document_id": "doc-1",
                "content": "This is my private policy document",
                "metadata": {"category": "policy"},
                "idempotency_key": "upload-1",
            },
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-1"
    submission = fake_workflow.submissions[0]
    assert submission["user_id"] == "user-1"
    assert submission["knowledge_base_id"] == "chat-1"
    assert submission["metadata"]["chat_id"] == "chat-1"
    assert submission["metadata"]["source_ref"] == "policy.txt"
    assert (tmp_path / submission["source_ref"]).read_text() == "This is my private policy document"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_chat_document_file_upload_and_search_use_rag(monkeypatch, tmp_path):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    monkeypatch.setenv("RAG_SOURCE_ROOT", str(tmp_path))
    from app.core.config import get_settings
    import app.api.v1.chats as chats_api

    get_settings.cache_clear()
    fake_chat = FakeChatService()
    fake_workflow = FakeWorkflow()
    fake_retrieval = FakeRetrievalService()
    monkeypatch.setattr(chats_api, "get_chat_service", lambda: fake_chat)
    monkeypatch.setattr(chats_api, "build_ingestion_workflow", lambda: fake_workflow)

    async def override_get_db():
        yield object()

    app = FastAPI()
    app.include_router(chats_api.router, prefix="/api/v1/chats")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_retrieval_service] = lambda: fake_retrieval
    token = create_access_token("user-1").token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/chats/chat-1/documents/upload",
            headers={"Authorization": f"Bearer {token}"},
            data={"source_ref": "policy.txt",
                  "document_id": "doc-upload-1", "metadata_json": '{"category":"policy"}'},
            files={"file": ("policy.txt", b"private policy body", "text/plain")},
        )
        search = await client.get(
            "/api/v1/chats/chat-1/documents/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"query": "policy", "limit": 3},
        )

    assert upload.status_code == 202
    assert fake_workflow.submissions[0]["metadata"]["chat_id"] == "chat-1"
    assert (tmp_path / fake_workflow.submissions[0]["source_ref"]).read_bytes() == b"private policy body"
    assert search.status_code == 200
    assert search.json()["hits"][0]["content"] == "Private policy snippet"
    request, user_id = fake_retrieval.calls[0]
    assert user_id == "user-1"
    assert request.knowledge_base_id == "chat-1"
    assert request.metadata_filters == {"chat_id": "chat-1"}
    get_settings.cache_clear()
