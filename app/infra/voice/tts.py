from __future__ import annotations

from html import escape

import httpx

from app.infra.voice.mcp import MCPStubTextToSpeech
from app.services.ports import TextToSpeechPort


class ElevenLabsTextToSpeech:
    """Text-to-speech via ElevenLabs REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        voice_id: str = "EXAVITQu4vr4xnSDxMaL",
        model_id: str = "eleven_turbo_v2_5",
        output_format: str = "mp3_44100_128",
        base_url: str = "https://api.elevenlabs.io/v1",
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model_id = model_id
        self._output_format = output_format
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._mime_type = _mime_from_elevenlabs_output_format(output_format)

    @property
    def mime_type(self) -> str:
        return self._mime_type

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None:
        clean = text.strip()
        if not clean:
            return None

        url = f"{self._base_url}/text-to-speech/{self._voice_id}"
        payload = {
            "text": clean,
            "model_id": self._model_id,
            "output_format": self._output_format,
        }
        headers = {
            "xi-api-key": self._api_key,
            "Content-Type": "application/json",
            "Accept": self._mime_type,
        }

        timeout = httpx.Timeout(timeout=self._timeout_seconds, connect=self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        if not response.content:
            raise RuntimeError("ElevenLabs TTS returned empty audio payload.")
        return response.content


class OpenAITextToSpeech:
    """Text-to-speech via OpenAI-compatible /audio/speech REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini-tts",
        voice: str = "alloy",
        audio_format: str = "mp3",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._audio_format = audio_format
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._mime_type = _mime_from_openai_audio_format(audio_format)

    @property
    def mime_type(self) -> str:
        return self._mime_type

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None:
        clean = text.strip()
        if not clean:
            return None

        url = f"{self._base_url}/audio/speech"
        payload = {
            "model": self._model,
            "voice": self._voice,
            "input": clean,
            "format": self._audio_format,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": self._mime_type,
        }

        timeout = httpx.Timeout(timeout=self._timeout_seconds, connect=self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()

        if not response.content:
            raise RuntimeError("OpenAI TTS returned empty audio payload.")
        return response.content


class AzureTextToSpeech:
    """Text-to-speech via Azure Speech REST API."""

    def __init__(
        self,
        *,
        speech_key: str,
        speech_region: str,
        voice_name: str = "en-US-JennyNeural",
        output_format: str = "audio-24khz-48kbitrate-mono-mp3",
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._speech_key = speech_key
        self._speech_region = speech_region
        self._voice_name = voice_name
        self._output_format = output_format
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._mime_type = _mime_from_azure_output_format(output_format)

    @property
    def mime_type(self) -> str:
        return self._mime_type

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None:
        clean = text.strip()
        if not clean:
            return None

        ssml = (
            "<speak version='1.0' xml:lang='en-US'>"
            f"<voice name='{escape(self._voice_name)}'>{escape(clean)}</voice>"
            "</speak>"
        )
        url = f"https://{self._speech_region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": self._speech_key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self._output_format,
            "User-Agent": "knowledge-ai-voice",
            "Accept": self._mime_type,
        }

        timeout = httpx.Timeout(timeout=self._timeout_seconds, connect=self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            response = await client.post(url, content=ssml.encode("utf-8"), headers=headers)
            response.raise_for_status()

        if not response.content:
            raise RuntimeError("Azure TTS returned empty audio payload.")
        return response.content


def resolve_text_to_speech_provider(
    *,
    provider: str,
    elevenlabs_api_key: str | None,
    elevenlabs_base_url: str,
    elevenlabs_voice_id: str,
    elevenlabs_model_id: str,
    elevenlabs_output_format: str,
    openai_api_key: str | None,
    openai_base_url: str,
    openai_model: str,
    openai_voice: str,
    openai_audio_format: str,
    azure_speech_key: str | None,
    azure_speech_region: str | None,
    azure_voice_name: str,
    azure_output_format: str,
    timeout_seconds: int,
) -> TextToSpeechPort:
    normalized = provider.strip().lower() or "auto"

    if normalized == "stub":
        return MCPStubTextToSpeech()

    if normalized == "elevenlabs":
        if elevenlabs_api_key:
            return ElevenLabsTextToSpeech(
                api_key=elevenlabs_api_key,
                voice_id=elevenlabs_voice_id,
                model_id=elevenlabs_model_id,
                output_format=elevenlabs_output_format,
                base_url=elevenlabs_base_url,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubTextToSpeech()

    if normalized == "openai":
        if openai_api_key:
            return OpenAITextToSpeech(
                api_key=openai_api_key,
                model=openai_model,
                voice=openai_voice,
                audio_format=openai_audio_format,
                base_url=openai_base_url,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubTextToSpeech()

    if normalized == "azure":
        if azure_speech_key and azure_speech_region:
            return AzureTextToSpeech(
                speech_key=azure_speech_key,
                speech_region=azure_speech_region,
                voice_name=azure_voice_name,
                output_format=azure_output_format,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubTextToSpeech()

    if normalized == "auto":
        if elevenlabs_api_key:
            return ElevenLabsTextToSpeech(
                api_key=elevenlabs_api_key,
                voice_id=elevenlabs_voice_id,
                model_id=elevenlabs_model_id,
                output_format=elevenlabs_output_format,
                base_url=elevenlabs_base_url,
                timeout_seconds=timeout_seconds,
            )
        if openai_api_key:
            return OpenAITextToSpeech(
                api_key=openai_api_key,
                model=openai_model,
                voice=openai_voice,
                audio_format=openai_audio_format,
                base_url=openai_base_url,
                timeout_seconds=timeout_seconds,
            )
        if azure_speech_key and azure_speech_region:
            return AzureTextToSpeech(
                speech_key=azure_speech_key,
                speech_region=azure_speech_region,
                voice_name=azure_voice_name,
                output_format=azure_output_format,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubTextToSpeech()

    return MCPStubTextToSpeech()


def _mime_from_elevenlabs_output_format(output_format: str) -> str:
    value = output_format.strip().lower()
    if value.startswith("mp3"):
        return "audio/mpeg"
    if value.startswith("pcm"):
        return "audio/pcm"
    if value.startswith("wav"):
        return "audio/wav"
    if value.startswith("ulaw"):
        return "audio/basic"
    return "application/octet-stream"


def _mime_from_openai_audio_format(audio_format: str) -> str:
    value = audio_format.strip().lower()
    mapping = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "pcm": "audio/pcm",
        "flac": "audio/flac",
        "opus": "audio/ogg",
    }
    return mapping.get(value, "application/octet-stream")


def _mime_from_azure_output_format(output_format: str) -> str:
    value = output_format.strip().lower()
    if "mp3" in value:
        return "audio/mpeg"
    if "riff" in value or "wav" in value:
        return "audio/wav"
    if "ogg" in value:
        return "audio/ogg"
    if "webm" in value:
        return "audio/webm"
    if "pcm" in value:
        return "audio/pcm"
    return "application/octet-stream"
