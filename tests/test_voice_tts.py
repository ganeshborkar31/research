import httpx
import pytest

from app.infra.voice import (
    AzureTextToSpeech,
    ElevenLabsTextToSpeech,
    OpenAITextToSpeech,
    resolve_text_to_speech_provider,
)
from app.infra.voice.mcp import MCPStubTextToSpeech


@pytest.mark.asyncio
async def test_elevenlabs_tts_returns_audio_and_mime_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "/text-to-speech/voice-1" in request.url.path
        assert request.headers.get("xi-api-key") == "eleven-key"
        return httpx.Response(200, content=b"ID3FAKE")

    tts = ElevenLabsTextToSpeech(
        api_key="eleven-key",
        voice_id="voice-1",
        output_format="mp3_44100_128",
        base_url="https://api.elevenlabs.io/v1",
        transport=httpx.MockTransport(handler),
    )

    audio = await tts.synthesize_chunk(text="hello there", session_id="s1")
    assert audio == b"ID3FAKE"
    assert tts.mime_type == "audio/mpeg"


@pytest.mark.asyncio
async def test_openai_tts_returns_audio_and_mime_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path.endswith("/audio/speech")
        assert request.headers.get("authorization") == "Bearer openai-key"
        return httpx.Response(200, content=b"WAVDATA")

    tts = OpenAITextToSpeech(
        api_key="openai-key",
        model="gpt-4o-mini-tts",
        voice="alloy",
        audio_format="wav",
        base_url="https://api.openai.com/v1",
        transport=httpx.MockTransport(handler),
    )

    audio = await tts.synthesize_chunk(text="hello there", session_id="s2")
    assert audio == b"WAVDATA"
    assert tts.mime_type == "audio/wav"


@pytest.mark.asyncio
async def test_azure_tts_returns_audio_and_mime_type():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.host == "eastus.tts.speech.microsoft.com"
        assert request.headers.get("ocp-apim-subscription-key") == "azure-key"
        assert request.headers.get("x-microsoft-outputformat") == "audio-24khz-48kbitrate-mono-mp3"
        assert b"en-US-JennyNeural" in request.content
        return httpx.Response(200, content=b"MP3DATA")

    tts = AzureTextToSpeech(
        speech_key="azure-key",
        speech_region="eastus",
        voice_name="en-US-JennyNeural",
        output_format="audio-24khz-48kbitrate-mono-mp3",
        transport=httpx.MockTransport(handler),
    )

    audio = await tts.synthesize_chunk(text="hello there", session_id="s3")
    assert audio == b"MP3DATA"
    assert tts.mime_type == "audio/mpeg"


def test_tts_provider_resolution_modes():
    provider = resolve_text_to_speech_provider(
        provider="auto",
        elevenlabs_api_key="eleven-key",
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model_id="eleven_turbo_v2_5",
        elevenlabs_output_format="mp3_44100_128",
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini-tts",
        openai_voice="alloy",
        openai_audio_format="mp3",
        azure_speech_key=None,
        azure_speech_region=None,
        azure_voice_name="en-US-JennyNeural",
        azure_output_format="audio-24khz-48kbitrate-mono-mp3",
        timeout_seconds=10,
    )
    assert isinstance(provider, ElevenLabsTextToSpeech)

    provider = resolve_text_to_speech_provider(
        provider="auto",
        elevenlabs_api_key=None,
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model_id="eleven_turbo_v2_5",
        elevenlabs_output_format="mp3_44100_128",
        openai_api_key="openai-key",
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini-tts",
        openai_voice="alloy",
        openai_audio_format="mp3",
        azure_speech_key=None,
        azure_speech_region=None,
        azure_voice_name="en-US-JennyNeural",
        azure_output_format="audio-24khz-48kbitrate-mono-mp3",
        timeout_seconds=10,
    )
    assert isinstance(provider, OpenAITextToSpeech)

    provider = resolve_text_to_speech_provider(
        provider="auto",
        elevenlabs_api_key=None,
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model_id="eleven_turbo_v2_5",
        elevenlabs_output_format="mp3_44100_128",
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini-tts",
        openai_voice="alloy",
        openai_audio_format="mp3",
        azure_speech_key="azure-key",
        azure_speech_region="eastus",
        azure_voice_name="en-US-JennyNeural",
        azure_output_format="audio-24khz-48kbitrate-mono-mp3",
        timeout_seconds=10,
    )
    assert isinstance(provider, AzureTextToSpeech)

    provider = resolve_text_to_speech_provider(
        provider="unknown",
        elevenlabs_api_key=None,
        elevenlabs_base_url="https://api.elevenlabs.io/v1",
        elevenlabs_voice_id="voice-1",
        elevenlabs_model_id="eleven_turbo_v2_5",
        elevenlabs_output_format="mp3_44100_128",
        openai_api_key=None,
        openai_base_url="https://api.openai.com/v1",
        openai_model="gpt-4o-mini-tts",
        openai_voice="alloy",
        openai_audio_format="mp3",
        azure_speech_key=None,
        azure_speech_region=None,
        azure_voice_name="en-US-JennyNeural",
        azure_output_format="audio-24khz-48kbitrate-mono-mp3",
        timeout_seconds=10,
    )
    assert isinstance(provider, MCPStubTextToSpeech)
