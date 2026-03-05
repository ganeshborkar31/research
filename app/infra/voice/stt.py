from __future__ import annotations

import base64
from typing import Any

import httpx

from app.infra.voice.mcp import MCPStubSpeechToText
from app.services.ports import SpeechToTextPort


class GeminiSpeechToText:
    """Speech-to-text using Gemini generateContent with inline audio data."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.0-flash",
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str:
        if not audio_bytes:
            return ""

        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "text": (
                                "Transcribe the speech from this audio. "
                                "Return only the spoken text without extra commentary."
                            )
                        },
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0,
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:generateContent?key={self._api_key}"
        )

        timeout = httpx.Timeout(timeout=self._timeout_seconds, connect=self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

        text = _extract_gemini_text(data)
        if not text:
            raise RuntimeError("Gemini STT returned empty transcription.")
        return text


class WhisperSpeechToText:
    """Speech-to-text using a Whisper-compatible HTTP API (OpenAI/Groq-compatible)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "whisper-1",
        timeout_seconds: int = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str:
        if not audio_bytes:
            return ""

        files = {
            "file": (f"{session_id}.wav", audio_bytes, mime_type),
        }
        form_data = {
            "model": self._model,
            "response_format": "json",
            "language": "en",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        url = f"{self._base_url}/audio/transcriptions"

        timeout = httpx.Timeout(timeout=self._timeout_seconds, connect=self._timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
            response = await client.post(url, headers=headers, data=form_data, files=files)
            response.raise_for_status()
            data = response.json()

        text = str(data.get("text", "")).strip()
        if not text:
            raise RuntimeError("Whisper STT returned empty transcription.")
        return text


def resolve_speech_to_text_provider(
    *,
    provider: str,
    gemini_api_key: str | None,
    gemini_model: str,
    whisper_api_key: str | None,
    whisper_base_url: str,
    whisper_model: str,
    timeout_seconds: int,
) -> SpeechToTextPort:
    normalized = provider.strip().lower() or "auto"

    if normalized == "stub":
        return MCPStubSpeechToText()

    if normalized == "gemini":
        if gemini_api_key:
            return GeminiSpeechToText(
                api_key=gemini_api_key,
                model=gemini_model,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubSpeechToText()

    if normalized == "whisper":
        if whisper_api_key:
            return WhisperSpeechToText(
                api_key=whisper_api_key,
                base_url=whisper_base_url,
                model=whisper_model,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubSpeechToText()

    if normalized == "auto":
        if gemini_api_key:
            return GeminiSpeechToText(
                api_key=gemini_api_key,
                model=gemini_model,
                timeout_seconds=timeout_seconds,
            )
        if whisper_api_key:
            return WhisperSpeechToText(
                api_key=whisper_api_key,
                base_url=whisper_base_url,
                model=whisper_model,
                timeout_seconds=timeout_seconds,
            )
        return MCPStubSpeechToText()

    return MCPStubSpeechToText()


def _extract_gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates", [])
    if not candidates:
        return ""

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not isinstance(parts, list):
        return ""

    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())

    return "\n".join(chunks).strip()
