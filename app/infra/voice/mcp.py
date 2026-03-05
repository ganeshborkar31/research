class MCPStubSpeechToText:
    """Phase-1 STT adapter for MCP voice events.

    This is a deterministic placeholder until a production streaming STT
    provider is integrated.
    """

    async def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        session_id: str,
    ) -> str:
        if not audio_bytes:
            return ""
        try:
            text = audio_bytes.decode("utf-8").strip()
            if text:
                return text
        except UnicodeDecodeError:
            pass
        return "Voice input received."


class MCPStubTextToSpeech:
    """Phase-1 TTS adapter for MCP voice events.

    Returns UTF-8 bytes as transport payload to validate streaming orchestration.
    Replace with a real audio TTS provider in the next phase.
    """

    @property
    def mime_type(self) -> str:
        return "application/octet-stream"

    async def synthesize_chunk(
        self,
        *,
        text: str,
        session_id: str,
    ) -> bytes | None:
        clean = text.strip()
        if not clean:
            return None
        return clean.encode("utf-8")
