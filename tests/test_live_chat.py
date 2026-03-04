import pytest


@pytest.mark.asyncio
async def test_live_chat_requires_auth(client):
    response = await client.post(
        "/api/v1/live-chat/respond",
        json={
            "session_id": "session-001",
            "message": "hello",
            "user_id": "user-abc",
        },
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_live_chat_respond(client, auth_headers):
    response = await client.post(
        "/api/v1/live-chat/respond",
        json={
            "session_id": "session-001",
            "message": "I want to improve my resume",
            "user_id": "user-abc",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-001"
    assert isinstance(payload["reply"], str)
    assert payload["reply"]


@pytest.mark.asyncio
async def test_twilio_voice_webhook_returns_twiml(client, auth_headers):
    response = await client.post(
        "/api/v1/live-chat/twilio/voice",
        data={
            "CallSid": "CA123456789",
            "From": "+12025550123",
            "SpeechResult": "I feel stressed about work",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Response>" in response.text
    assert "<Say>" in response.text
    assert "<Gather" in response.text
