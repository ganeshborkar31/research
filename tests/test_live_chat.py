import pytest


@pytest.mark.asyncio
async def test_voice_agents_requires_auth(client):
    response = await client.get("/api/v1/live-chat/voice/agents")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_voice_agents_endpoint_returns_profiles(client, auth_headers):
    response = await client.get("/api/v1/live-chat/voice/agents", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert any(agent["agent_id"] == "general" for agent in payload)
