import base64
import logging
from collections import defaultdict, deque
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.infra.llm import GeminiClient, LangChainChatGateway, LangGraphChatGateway
from app.infra.voice import resolve_speech_to_text_provider, resolve_text_to_speech_provider
from app.schemas.voice_agent import VoiceAgentProfileOut
from app.services.ports import ChatRuntimePort, SpeechToTextPort, TextToSpeechPort
from app.services.voice_agent_registry import (
    VoiceAgentProfile,
    list_voice_agent_profiles,
    resolve_voice_agent_profile,
)


logger = logging.getLogger(__name__)


class VoiceChatService:
    """Orchestrates MCP-style voice chat events for real-time sessions."""

    def __init__(
        self,
        *,
        runtime: ChatRuntimePort | None = None,
        stt: SpeechToTextPort | None = None,
        tts: TextToSpeechPort | None = None,
        max_history_turns: int = 12,
    ) -> None:
        settings = get_settings()
        if runtime is None:
            gemini_client = GeminiClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
            )
            if settings.use_langgraph_chat and LangGraphChatGateway:
                runtime = LangGraphChatGateway(gemini_client=gemini_client)
            else:
                if settings.use_langgraph_chat and not LangGraphChatGateway:
                    logger.warning(
                        "use_langgraph_chat=true, but langgraph is unavailable; using LangChain."
                    )
                runtime = LangChainChatGateway(gemini_client=gemini_client)

        self._runtime = runtime
        if stt is not None:
            self._stt = stt
        else:
            self._stt = resolve_speech_to_text_provider(
                provider=settings.voice_stt_provider,
                gemini_api_key=settings.gemini_api_key,
                gemini_model=settings.voice_stt_gemini_model,
                whisper_api_key=settings.whisper_api_key,
                whisper_base_url=settings.whisper_base_url,
                whisper_model=settings.whisper_model,
                timeout_seconds=settings.voice_stt_timeout_seconds,
            )
            logger.info("Voice STT provider: %s", self._stt.__class__.__name__)
        if tts is not None:
            self._tts = tts
        else:
            self._tts = resolve_text_to_speech_provider(
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
            logger.info("Voice TTS provider: %s", self._tts.__class__.__name__)
        self._default_agent = resolve_voice_agent_profile(settings.default_voice_agent_id)
        self._history: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_history_turns * 2)
        )
        self._session_agent: dict[str, str] = {}

    def list_agents(self) -> list[VoiceAgentProfileOut]:
        return [
            VoiceAgentProfileOut(
                agent_id=profile.agent_id,
                name=profile.name,
                description=profile.description,
                domain=profile.domain,
            )
            for profile in list_voice_agent_profiles()
        ]

    def get_default_agent(self) -> VoiceAgentProfileOut:
        profile = self._default_agent
        return VoiceAgentProfileOut(
            agent_id=profile.agent_id,
            name=profile.name,
            description=profile.description,
            domain=profile.domain,
        )

    async def process_mcp_event(
        self,
        *,
        session_id: str,
        user_id: str | None,
        event: dict,
    ) -> AsyncIterator[dict]:
        session_key = self._session_key(session_id, user_id)
        event_type = str(event.get("type", "")).strip()
        if event_type == "agent.set":
            requested_agent_id = str(event.get("agent_id", "")).strip()
            profile = resolve_voice_agent_profile(requested_agent_id or self._default_agent.agent_id)
            self._session_agent[session_key] = profile.agent_id
            yield {
                "type": "agent.updated",
                "session_id": session_id,
                "agent": self._profile_payload(profile),
            }
            return

        if event_type == "input.text":
            text = str(event.get("text", "")).strip()
            if not text:
                yield {"type": "error", "detail": "input.text requires non-empty 'text'."}
                return
            async for out in self._run_turn(
                session_id=session_id,
                user_id=user_id,
                user_message=text,
            ):
                yield out
            return

        if event_type == "input.audio":
            audio_b64 = str(event.get("audio_b64", "")).strip()
            if not audio_b64:
                yield {"type": "error", "detail": "input.audio requires 'audio_b64'."}
                return

            mime_type = str(event.get("mime_type", "audio/pcm")).strip() or "audio/pcm"
            try:
                audio_bytes = base64.b64decode(audio_b64, validate=True)
            except Exception:
                yield {"type": "error", "detail": "Invalid base64 audio payload."}
                return

            try:
                user_message = await self._stt.transcribe_audio(
                    audio_bytes=audio_bytes,
                    mime_type=mime_type,
                    session_id=session_id,
                )
            except Exception as exc:
                logger.warning("STT transcription failed for session_id=%s: %s", session_id, exc)
                yield {"type": "error", "detail": "Speech transcription failed."}
                return
            user_message = user_message.strip()
            if not user_message:
                yield {"type": "error", "detail": "Speech transcription is empty."}
                return

            yield {
                "type": "stt.final",
                "session_id": session_id,
                "text": user_message,
            }
            async for out in self._run_turn(
                session_id=session_id,
                user_id=user_id,
                user_message=user_message,
            ):
                yield out
            return

        yield {"type": "error", "detail": f"Unsupported MCP event type: {event_type or 'unknown'}"}

    async def _run_turn(
        self,
        *,
        session_id: str,
        user_id: str | None,
        user_message: str,
    ) -> AsyncIterator[dict]:
        session_key = self._session_key(session_id, user_id)
        profile = resolve_voice_agent_profile(self._session_agent.get(session_key))
        history = list(self._history[session_key])
        assistant_chunks: list[str] = []

        async for chunk in self._runtime.stream_reply(
            system_prompt=profile.system_prompt,
            user_message=user_message,
            history=history,
            retrieval_context="No retrieval context available.",
        ):
            if not chunk:
                continue

            assistant_chunks.append(chunk)
            yield {
                "type": "llm.chunk",
                "session_id": session_id,
                "content": chunk,
            }

            try:
                audio_bytes = await self._tts.synthesize_chunk(text=chunk, session_id=session_id)
            except Exception as exc:
                logger.warning("TTS synthesis failed for session_id=%s: %s", session_id, exc)
                audio_bytes = None
            if audio_bytes:
                yield {
                    "type": "tts.chunk",
                    "session_id": session_id,
                    "mime_type": self._tts.mime_type,
                    "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                }

        assistant_reply = "".join(assistant_chunks).strip()
        self._history[session_key].append({"role": "user", "content": user_message})
        self._history[session_key].append({"role": "assistant", "content": assistant_reply})
        yield {
            "type": "response.done",
            "session_id": session_id,
            "agent_id": profile.agent_id,
            "text": assistant_reply,
        }

    def _session_key(self, session_id: str, user_id: str | None) -> str:
        if user_id:
            return f"{user_id}:{session_id}"
        return session_id

    def _profile_payload(self, profile: VoiceAgentProfile) -> dict[str, str]:
        return {
            "agent_id": profile.agent_id,
            "name": profile.name,
            "description": profile.description,
            "domain": profile.domain,
        }


_voice_chat_service: VoiceChatService | None = None


def get_voice_chat_service() -> VoiceChatService:
    global _voice_chat_service
    if _voice_chat_service is None:
        _voice_chat_service = VoiceChatService()
    return _voice_chat_service
