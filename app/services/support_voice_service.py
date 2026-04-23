from __future__ import annotations

import base64
import logging
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infra.voice import resolve_speech_to_text_provider, resolve_text_to_speech_provider
from app.schemas.support_agent import SupportChatResponse, SupportVoiceResponse
from app.services.ports import SpeechToTextPort, TextToSpeechPort
from app.services.support_agent_service import get_support_agent_service


logger = logging.getLogger(__name__)


class SupportVoiceService:
    def __init__(
        self,
        *,
        stt: SpeechToTextPort | None = None,
        tts: TextToSpeechPort | None = None,
    ) -> None:
        settings = get_settings()
        self._agent = get_support_agent_service()
        self._stt = stt or resolve_speech_to_text_provider(
            provider=settings.voice_stt_provider,
            gemini_api_key=settings.gemini_api_key,
            gemini_model=settings.voice_stt_gemini_model,
            whisper_api_key=settings.whisper_api_key,
            whisper_base_url=settings.whisper_base_url,
            whisper_model=settings.whisper_model,
            timeout_seconds=settings.voice_stt_timeout_seconds,
        )
        self._tts = tts or resolve_text_to_speech_provider(
            provider=settings.voice_tts_provider,
            elevenlabs_api_key=settings.elevenlabs_api_key,
            elevenlabs_base_url=settings.elevenlabs_base_url,
            elevenlabs_voice_id=settings.voice_tts_elevenlabs_voice_id,
            elevenlabs_model_id=settings.voice_tts_elevenlabs_model_id,
            elevenlabs_output_format=settings.voice_tts_elevenlabs_output_format,
            openai_api_key=settings.openai_api_key,
            openai_base_url=settings.openai_base_url,
            openai_model=settings.voice_tts_openai_model,
            openai_voice=settings.voice_tts_openai_voice,
            openai_audio_format=settings.voice_tts_openai_format,
            azure_speech_key=settings.azure_speech_key,
            azure_speech_region=settings.azure_speech_region,
            azure_voice_name=settings.voice_tts_azure_voice_name,
            azure_output_format=settings.voice_tts_azure_output_format,
            timeout_seconds=settings.voice_tts_timeout_seconds,
        )

    async def handle_voice(
        self,
        *,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        chat_id: str | None,
        audio_b64: str | None,
        text: str | None,
        mime_type: str,
        return_audio: bool,
        kb_user_id: str | None,
    ) -> SupportVoiceResponse:
        session_id = f"support-voice-{uuid4()}"
        message_text = (text or "").strip()
        if not message_text and audio_b64:
            try:
                audio_bytes = base64.b64decode(audio_b64, validate=True)
            except Exception as exc:
                logger.warning("Invalid base64 audio payload: %s", exc)
                raise ValueError("Invalid base64 audio payload.") from exc

            message_text = await self._stt.transcribe_audio(
                audio_bytes=audio_bytes,
                mime_type=mime_type,
                session_id=session_id,
            )

        message_text = message_text.strip()
        if not message_text:
            raise ValueError("Either text or audio_b64 must be provided.")

        support_reply: SupportChatResponse = await self._agent.reply(
            db=db,
            tenant_id=tenant_id,
            user_id=user_id,
            message=message_text,
            chat_id=chat_id,
            kb_user_id=kb_user_id,
        )

        audio_out_b64: str | None = None
        mime_out: str | None = None
        if return_audio:
            try:
                audio_bytes = await self._tts.synthesize_chunk(
                    text=support_reply.reply,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.warning("TTS synthesis failed: %s", exc)
                audio_bytes = None

            if audio_bytes:
                audio_out_b64 = base64.b64encode(audio_bytes).decode("ascii")
                mime_out = self._tts.mime_type

        return SupportVoiceResponse(
            chat_id=support_reply.chat_id,
            text=support_reply.reply,
            audio_b64=audio_out_b64,
            mime_type=mime_out,
        )


_support_voice_service: SupportVoiceService | None = None


def get_support_voice_service() -> SupportVoiceService:
    global _support_voice_service
    if _support_voice_service is None:
        _support_voice_service = SupportVoiceService()
    return _support_voice_service
