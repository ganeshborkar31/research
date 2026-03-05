from typing import Protocol


class SpeechToTextPort(Protocol):
    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str: ...


class TextToSpeechPort(Protocol):
    @property
    def mime_type(self) -> str: ...

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None: ...
