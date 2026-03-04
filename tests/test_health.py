import pytest


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/api/v1/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_ok(client, monkeypatch):
    async def _ok():
        return None

    monkeypatch.setattr("app.api.health.postgres.check_postgres", _ok)
    monkeypatch.setattr("app.api.health.redis.check_redis", _ok)
    monkeypatch.setattr("app.api.health.qdrant.check_qdrant", _ok)
    monkeypatch.setattr("app.api.health.rabbitmq.check_rabbitmq", _ok)

    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_readiness_error(client, monkeypatch):
    async def _boom():
        raise RuntimeError("service unavailable")

    async def _ok():
        return None

    monkeypatch.setattr("app.api.health.postgres.check_postgres", _boom)
    monkeypatch.setattr("app.api.health.redis.check_redis", _ok)
    monkeypatch.setattr("app.api.health.qdrant.check_qdrant", _ok)
    monkeypatch.setattr("app.api.health.rabbitmq.check_rabbitmq", _ok)

    response = await client.get("/api/v1/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert "service unavailable" in body["detail"]
