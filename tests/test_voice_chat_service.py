import base64
from collections.abc import AsyncIterator

import pytest

from app.services.voice_chat_service import VoiceChatService


class _FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> str:
        return "unused"

    async def stream_reply(
        self,
        *,
        system_prompt: str,
        user_message: str,
        history: list[dict[str, str]],
        retrieval_context: str,
    ) -> AsyncIterator[str]:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "history": list(history),
                "retrieval_context": retrieval_context,
            }
        )
        for chunk in ("Hello", " ", "there"):
            yield chunk


class _FakeSTT:
    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str:
        return f"transcribed:{audio_bytes.decode('utf-8')}"


class _FakeTTS:
    @property
    def mime_type(self) -> str:
        return "audio/test"

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None:
        return f"audio:{text}".encode("utf-8")


class _FailingSTT:
    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str:
        raise RuntimeError("stt provider unavailable")


@pytest.mark.asyncio
async def test_voice_chat_text_event_streams_llm_and_tts():
    runtime = _FakeRuntime()
    service = VoiceChatService(runtime=runtime, stt=_FakeSTT(), tts=_FakeTTS())

    events = [
        event
        async for event in service.process_mcp_event(
            session_id="s1",
            user_id="u1",
            event={"type": "input.text", "text": "hi"},
        )
    ]

    assert [event["type"] for event in events] == [
        "llm.chunk",
        "tts.chunk",
        "llm.chunk",
        "tts.chunk",
        "llm.chunk",
        "tts.chunk",
        "response.done",
    ]
    assert events[-1]["text"] == "Hello there"
    assert runtime.calls[0]["user_message"] == "hi"
    assert runtime.calls[0]["history"] == []


@pytest.mark.asyncio
async def test_voice_chat_audio_event_emits_stt_then_streams():
    runtime = _FakeRuntime()
    service = VoiceChatService(runtime=runtime, stt=_FakeSTT(), tts=_FakeTTS())
    payload = base64.b64encode(b"hello").decode("ascii")

    events = [
        event
        async for event in service.process_mcp_event(
            session_id="s2",
            user_id="u2",
            event={"type": "input.audio", "audio_b64": payload, "mime_type": "audio/wav"},
        )
    ]

    assert events[0]["type"] == "stt.final"
    assert events[0]["text"] == "transcribed:hello"
    assert runtime.calls[0]["user_message"] == "transcribed:hello"
    assert events[-1]["type"] == "response.done"


@pytest.mark.asyncio
async def test_voice_chat_audio_transcription_failure_returns_error():
    runtime = _FakeRuntime()
    service = VoiceChatService(runtime=runtime, stt=_FailingSTT(), tts=_FakeTTS())
    payload = base64.b64encode(b"hello").decode("ascii")

    events = [
        event
        async for event in service.process_mcp_event(
            session_id="s2-fail",
            user_id="u2",
            event={"type": "input.audio", "audio_b64": payload, "mime_type": "audio/wav"},
        )
    ]

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["detail"] == "Speech transcription failed."
    assert runtime.calls == []


@pytest.mark.asyncio
async def test_voice_chat_agent_set_updates_prompt_profile():
    runtime = _FakeRuntime()
    service = VoiceChatService(runtime=runtime, stt=_FakeSTT(), tts=_FakeTTS())

    set_events = [
        event
        async for event in service.process_mcp_event(
            session_id="s-agent",
            user_id="u-agent",
            event={"type": "agent.set", "agent_id": "interview"},
        )
    ]
    assert set_events[0]["type"] == "agent.updated"
    assert set_events[0]["agent"]["agent_id"] == "interview"

    _ = [
        event
        async for event in service.process_mcp_event(
            session_id="s-agent",
            user_id="u-agent",
            event={"type": "input.text", "text": "mock interview me"},
        )
    ]
    assert "interview coach" in runtime.calls[0]["system_prompt"].lower()


@pytest.mark.asyncio
async def test_voice_chat_unsupported_event_returns_error():
    service = VoiceChatService(runtime=_FakeRuntime(), stt=_FakeSTT(), tts=_FakeTTS())
    events = [
        event
        async for event in service.process_mcp_event(
            session_id="s3",
            user_id="u3",
            event={"type": "unknown"},
        )
    ]
    assert len(events) == 1
    assert events[0]["type"] == "error"


def test_voice_chat_service_lists_default_agents():
    service = VoiceChatService(runtime=_FakeRuntime(), stt=_FakeSTT(), tts=_FakeTTS())
    agents = service.list_agents()
    assert any(agent.agent_id == "general" for agent in agents)
