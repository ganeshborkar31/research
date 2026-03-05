import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# Ensure project root is importable in all pytest entrypoints.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


@pytest_asyncio.fixture
async def client(monkeypatch):
    # Keep tests deterministic and offline by disabling external LLM calls.
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")

    from app.core.config import get_settings
    import app.services.voice_chat_service as voice_chat_service_module
    from app.main import app

    get_settings.cache_clear()
    voice_chat_service_module._voice_chat_service = None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    voice_chat_service_module._voice_chat_service = None
    get_settings.cache_clear()


@pytest.fixture
def auth_headers(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings
    from app.services.jwt_service import create_access_token

    get_settings.cache_clear()
    token = create_access_token("test-user-id", "tenant-1").token
    return {"Authorization": f"Bearer {token}"}
