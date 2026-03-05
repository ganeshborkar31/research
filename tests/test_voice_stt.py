import json

import httpx
import pytest

from app.infra.voice import GeminiSpeechToText, WhisperSpeechToText, resolve_speech_to_text_provider
from app.infra.voice.mcp import MCPStubSpeechToText


@pytest.mark.asyncio
async def test_gemini_stt_transcribes_audio_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith(":generateContent")
        payload = json.loads(request.content.decode("utf-8"))
        inline_data = payload["contents"][0]["parts"][1]["inline_data"]
        assert inline_data["mime_type"] == "audio/wav"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "hello from gemini"},
                            ]
                        }
                    }
                ]
            },
        )

    stt = GeminiSpeechToText(
        api_key="test-key",
        model="gemini-2.0-flash",
        transport=httpx.MockTransport(handler),
    )

    text = await stt.transcribe_audio(
        audio_bytes=b"fake-wav-bytes",
        mime_type="audio/wav",
        session_id="s1",
    )
    assert text == "hello from gemini"


@pytest.mark.asyncio
async def test_whisper_stt_transcribes_audio_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/audio/transcriptions")
        assert request.headers.get("authorization") == "Bearer whisper-key"
        return httpx.Response(
            200,
            json={"text": "hello from whisper"},
        )

    stt = WhisperSpeechToText(
        api_key="whisper-key",
        base_url="https://example.com/v1",
        model="whisper-1",
        transport=httpx.MockTransport(handler),
    )

    text = await stt.transcribe_audio(
        audio_bytes=b"fake-wav-bytes",
        mime_type="audio/wav",
        session_id="s2",
    )
    assert text == "hello from whisper"


def test_stt_provider_resolution_modes():
    provider = resolve_speech_to_text_provider(
        provider="auto",
        gemini_api_key="gem-key",
        gemini_model="gemini-2.0-flash",
        whisper_api_key=None,
        whisper_base_url="https://api.openai.com/v1",
        whisper_model="whisper-1",
        timeout_seconds=10,
    )
    assert isinstance(provider, GeminiSpeechToText)

    provider = resolve_speech_to_text_provider(
        provider="auto",
        gemini_api_key=None,
        gemini_model="gemini-2.0-flash",
        whisper_api_key="whisper-key",
        whisper_base_url="https://api.openai.com/v1",
        whisper_model="whisper-1",
        timeout_seconds=10,
    )
    assert isinstance(provider, WhisperSpeechToText)

    provider = resolve_speech_to_text_provider(
        provider="gemini",
        gemini_api_key=None,
        gemini_model="gemini-2.0-flash",
        whisper_api_key=None,
        whisper_base_url="https://api.openai.com/v1",
        whisper_model="whisper-1",
        timeout_seconds=10,
    )
    assert isinstance(provider, MCPStubSpeechToText)

    provider = resolve_speech_to_text_provider(
        provider="unknown",
        gemini_api_key=None,
        gemini_model="gemini-2.0-flash",
        whisper_api_key=None,
        whisper_base_url="https://api.openai.com/v1",
        whisper_model="whisper-1",
        timeout_seconds=10,
    )
    assert isinstance(provider, MCPStubSpeechToText)
