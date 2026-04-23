import pytest


class _StubVoiceChatService:
    async def process_mcp_event(self, *, session_id: str, user_id: str | None, event: dict):
        text = str(event.get("text", "")).strip()
        yield {
            "type": "response.done",
            "session_id": session_id,
            "text": f"stub-reply:{text}",
        }


class _StubTwilioService:
    async def send_sms(self, *, to_number: str, body: str, from_number: str | None = None, status_callback_url: str | None = None):
        return {
            "sid": "SM123",
            "status": "queued",
            "to": to_number,
            "from": from_number or "+15550000000",
            "direction": "outbound-api",
        }

    async def create_call(self, *, to_number: str, twiml_url: str, from_number: str | None = None, status_callback_url: str | None = None):
        return {
            "sid": "CA123",
            "status": "queued",
            "to": to_number,
            "from": from_number or "+15550000000",
            "direction": "outbound-api",
        }

    def voice_gather_twiml(self, *, say_text: str, action_url: str) -> str:
        return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>{say_text}</Say><Gather action=\"{action_url}\" method=\"POST\"/></Response>"

    def sms_twiml(self, *, message: str) -> str:
        return f"<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Message>{message}</Message></Response>"


@pytest.mark.asyncio
async def test_twilio_send_sms_requires_auth(client):
    response = await client.post(
        "/api/v1/twilio/sms/send",
        json={"to": "+15551112222", "message": "hello"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_twilio_send_sms_outbound(client, auth_headers, monkeypatch):
    import app.api.v1.twilio as twilio_api

    monkeypatch.setattr(twilio_api, "get_twilio_service", lambda: _StubTwilioService())

    response = await client.post(
        "/api/v1/twilio/sms/send",
        json={"to": "+15551112222", "message": "hello"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sid"] == "SM123"
    assert payload["to"] == "+15551112222"


@pytest.mark.asyncio
async def test_twilio_voice_incoming_returns_twiml(client, monkeypatch):
    import app.api.v1.twilio as twilio_api

    monkeypatch.setattr(twilio_api, "get_twilio_service", lambda: _StubTwilioService())

    response = await client.post(
        "/api/v1/twilio/voice/incoming",
        data={"CallSid": "CA999"},
    )

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "<Response>" in response.text
    assert "<Gather" in response.text


@pytest.mark.asyncio
async def test_twilio_voice_respond_uses_ai_reply(client, monkeypatch):
    import app.api.v1.twilio as twilio_api

    monkeypatch.setattr(twilio_api, "get_twilio_service", lambda: _StubTwilioService())
    monkeypatch.setattr(twilio_api, "get_voice_chat_service", lambda: _StubVoiceChatService())

    response = await client.post(
        "/api/v1/twilio/voice/respond?session_id=CA888",
        data={"SpeechResult": "Need billing help", "From": "+15550009999"},
    )

    assert response.status_code == 200
    assert "stub-reply:Need billing help" in response.text


@pytest.mark.asyncio
async def test_twilio_sms_incoming_uses_ai_reply(client, monkeypatch):
    import app.api.v1.twilio as twilio_api

    monkeypatch.setattr(twilio_api, "get_twilio_service", lambda: _StubTwilioService())
    monkeypatch.setattr(twilio_api, "get_voice_chat_service", lambda: _StubVoiceChatService())

    response = await client.post(
        "/api/v1/twilio/sms/incoming",
        data={"Body": "where is my order", "From": "+15554443333"},
    )

    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    assert "stub-reply:where is my order" in response.text


@pytest.mark.asyncio
async def test_twilio_create_outbound_call(client, auth_headers, monkeypatch):
    import app.api.v1.twilio as twilio_api

    monkeypatch.setattr(twilio_api, "get_twilio_service", lambda: _StubTwilioService())

    response = await client.post(
        "/api/v1/twilio/voice/call",
        json={"to": "+15556667777", "welcome_message": "Hello from support"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["sid"] == "CA123"
    assert payload["to"] == "+15556667777"
