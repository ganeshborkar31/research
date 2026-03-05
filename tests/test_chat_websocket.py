import asyncio
import json
import socket
import sys
from collections.abc import AsyncIterator
from types import ModuleType, SimpleNamespace

import pytest
import uvicorn
import websockets
from fastapi import FastAPI

from app.services.jwt_service import create_access_token


class _FakeChatService:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(
            id="chat-1",
            tenant_id="tenant-1",
            user_id="user-1",
        )
        self.messages: list[str] = []

    async def get_chat(self, db, *, chat_id: str, tenant_id: str, user_id: str):
        if chat_id != self.chat.id:
            return None
        if tenant_id != self.chat.tenant_id or user_id != self.chat.user_id:
            return None
        return self.chat

    async def stream_message(self, db, *, chat, message: str) -> AsyncIterator[str]:
        self.messages.append(message)
        for chunk in ("Hello", " ", "world"):
            yield chunk


def _install_fake_qdrant_modules(monkeypatch) -> None:
    class _Stub:
        def __init__(self, *args, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _AsyncQdrantClient:
        def __init__(self, *args, **kwargs):
            return None

        async def get_collections(self):
            return {}

        async def close(self):
            return None

        async def collection_exists(self, *args, **kwargs):
            return True

        async def create_collection(self, *args, **kwargs):
            return None

        async def create_payload_index(self, *args, **kwargs):
            return None

        async def upsert(self, *args, **kwargs):
            return None

        async def query_points(self, *args, **kwargs):
            return SimpleNamespace(points=[])

    fake_qdrant = ModuleType("qdrant_client")
    fake_qdrant.AsyncQdrantClient = _AsyncQdrantClient

    fake_models = ModuleType("qdrant_client.http.models")
    fake_models.PointStruct = _Stub
    fake_models.Filter = _Stub
    fake_models.FieldCondition = _Stub
    fake_models.MatchValue = _Stub
    fake_models.VectorParams = _Stub
    fake_models.ScoredPoint = _Stub
    fake_models.Distance = SimpleNamespace(COSINE="cosine")
    fake_models.PayloadSchemaType = SimpleNamespace(KEYWORD="keyword")

    fake_http = ModuleType("qdrant_client.http")
    fake_http.models = fake_models

    monkeypatch.setitem(sys.modules, "qdrant_client", fake_qdrant)
    monkeypatch.setitem(sys.modules, "qdrant_client.http", fake_http)
    monkeypatch.setitem(sys.modules, "qdrant_client.http.models", fake_models)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.asyncio
async def test_chat_websocket_streaming_events(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    _install_fake_qdrant_modules(monkeypatch)

    from app.core.config import get_settings
    from app.db.session import get_db
    import app.api.v1.chats as chats_api
    from app.api.v1.chats import router as chats_router

    get_settings.cache_clear()

    fake_service = _FakeChatService()
    monkeypatch.setattr(chats_api, "get_chat_service", lambda: fake_service)

    async def override_get_db():
        yield object()

    app = FastAPI()
    app.include_router(chats_router, prefix="/api/v1/chats")
    app.dependency_overrides[get_db] = override_get_db

    try:
        port = _free_port()
    except PermissionError:
        pytest.skip("Socket binding is not permitted in this environment.")
    config = uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())

    try:
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("Uvicorn test server failed to start.")

        token = create_access_token("user-1", "tenant-1").token
        ws_url = f"ws://127.0.0.1:{port}/api/v1/chats/chat-1/ws?token={token}"

        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"message": "Hi bot"}))
            events = [json.loads(await ws.recv()) for _ in range(5)]

        assert events[0] == {"type": "start", "chat_id": "chat-1"}
        assert events[1] == {"type": "chunk", "content": "Hello"}
        assert events[2] == {"type": "chunk", "content": " "}
        assert events[3] == {"type": "chunk", "content": "world"}
        assert events[4] == {"type": "done", "chat_id": "chat-1"}
        assert fake_service.messages == ["Hi bot"]
    finally:
        server.should_exit = True
        await server_task
        get_settings.cache_clear()
