from collections import defaultdict, deque

from app.core.config import get_settings
from app.domain.live_chat import LiveChatRequest, LiveChatResponse
from app.infra.llm import GeminiClient


SYSTEM_PROMPT = (
    "You are a practical AI assistant for live chat and voice conversations. "
    "Be clear, brief, and action-oriented."
)


class LiveChatService:
    def __init__(self, max_history_turns: int = 12) -> None:
        settings = get_settings()
        self._llm = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )
        self._history: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max_history_turns * 2)
        )

    async def respond(self, payload: LiveChatRequest) -> LiveChatResponse:
        session_key = self._session_key(payload.session_id, payload.user_id)
        history = list(self._history[session_key])

        reply = await self._llm.generate_reply(
            system_prompt=SYSTEM_PROMPT,
            user_message=payload.message,
            history=history,
        )

        self._history[session_key].append(
            {
                "role": "user",
                "content": payload.message,
            }
        )
        self._history[session_key].append(
            {
                "role": "assistant",
                "content": reply,
            }
        )

        return LiveChatResponse(
            session_id=payload.session_id,
            reply=reply,
        )

    async def respond_voice(
        self,
        *,
        call_sid: str,
        speech_text: str,
        caller_id: str | None = None,
    ) -> str:
        response = await self.respond(
            LiveChatRequest(
                session_id=call_sid,
                message=speech_text,
                user_id=caller_id,
            )
        )
        return response.reply

    def _session_key(self, session_id: str, user_id: str | None) -> str:
        if user_id:
            return f"{user_id}:{session_id}"
        return session_id


_live_chat_service: LiveChatService | None = None


def get_live_chat_service() -> LiveChatService:
    global _live_chat_service
    if _live_chat_service is None:
        _live_chat_service = LiveChatService()
    return _live_chat_service
