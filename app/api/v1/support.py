import base64

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import (
    get_access_claims_from_token,
    get_bearer_token_from_header,
    get_current_access_claims,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.infra.voice import resolve_speech_to_text_provider, resolve_text_to_speech_provider
from app.schemas.mcp_voice import MCPClientEvent, MCPInputAudioIn, MCPInputTextIn, MCPSessionStartIn
from app.schemas.support_agent import (
    SupportChatRequest,
    SupportChatResponse,
    SupportVoiceRequest,
    SupportVoiceResponse,
)
from app.services.support_agent_service import get_support_agent_service
from app.services.support_chat_store import SupportChatStore
from app.services.support_voice_service import get_support_voice_service


router = APIRouter()


@router.post("/chat", response_model=SupportChatResponse)
async def support_chat(
    payload: SupportChatRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> SupportChatResponse:
    service = get_support_agent_service()
    response = await service.reply(
        db=db,
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
        message=payload.message,
        chat_id=payload.chat_id,
        kb_user_id=payload.kb_user_id,
    )
    return response


@router.post("/voice", response_model=SupportVoiceResponse)
async def support_voice(
    payload: SupportVoiceRequest,
    db: AsyncSession = Depends(get_db),
    claims: dict = Depends(get_current_access_claims),
) -> SupportVoiceResponse:
    service = get_support_voice_service()
    try:
        return await service.handle_voice(
            db=db,
            tenant_id=claims["tenant_id"],
            user_id=claims["sub"],
            chat_id=payload.chat_id,
            audio_b64=payload.audio_b64,
            text=payload.text,
            mime_type=payload.mime_type,
            return_audio=payload.return_audio,
            kb_user_id=payload.kb_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.websocket("/chat/ws")
async def support_chat_websocket(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> None:
    token = get_bearer_token_from_header(websocket.headers.get("authorization"))
    if not token:
        token = websocket.query_params.get("token")

    try:
        claims = get_access_claims_from_token(token or "")
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    service = get_support_agent_service()
    store = SupportChatStore()
    chat_id = websocket.query_params.get("chat_id")
    kb_user_id = websocket.query_params.get("kb_user_id")
    chat = await store.get_or_create_chat(
        db,
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
        chat_id=chat_id,
    )
    chat_id = chat.id

    await websocket.accept()
    await websocket.send_json(
        {"type": "session.started", "protocol": "support.chat.v1", "chat_id": chat_id}
    )

    try:
        while True:
            payload = await websocket.receive_json()
            try:
                event = MCPClientEvent.model_validate(payload)
            except ValidationError:
                await websocket.send_json({"type": "error", "detail": "Invalid event payload."})
                continue

            event_type = event.type.strip()

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if event_type == "session.start":
                try:
                    start_event = MCPSessionStartIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid session.start payload."}
                    )
                    continue

                requested_chat_id = (start_event.session_id or "").strip() or None
                if requested_chat_id or chat_id:
                    chat = await store.get_or_create_chat(
                        db,
                        tenant_id=claims["tenant_id"],
                        user_id=claims["sub"],
                        chat_id=requested_chat_id or chat_id,
                    )
                    chat_id = chat.id

                if "kb_user_id" in payload:
                    kb_user_id = str(payload.get("kb_user_id") or "").strip() or None

                await websocket.send_json(
                    {"type": "session.started", "protocol": "support.chat.v1", "chat_id": chat_id}
                )
                continue

            if event_type == "session.stop":
                await websocket.send_json({"type": "session.stopped", "chat_id": chat_id})
                await websocket.close(code=1000, reason="Session stopped by client")
                return

            if event_type == "input.text":
                try:
                    parsed_text = MCPInputTextIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid input.text payload."}
                    )
                    continue

                try:
                    stream_result = await service.stream_message(
                        db=db,
                        tenant_id=claims["tenant_id"],
                        user_id=claims["sub"],
                        message=parsed_text.text,
                        chat_id=chat_id,
                        kb_user_id=kb_user_id,
                    )
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue

                chat_id = stream_result.chat_id
                await websocket.send_json({"type": "response.started", "chat_id": chat_id})

                chunks: list[str] = []
                async for chunk in stream_result.chunks:
                    chunks.append(chunk)
                    await websocket.send_json(
                        {"type": "llm.chunk", "chat_id": chat_id, "content": chunk}
                    )

                await websocket.send_json(
                    {
                        "type": "response.done",
                        "chat_id": chat_id,
                        "text": "".join(chunks).strip(),
                        "policy_blocked": stream_result.policy_blocked,
                        "retrieval_context": stream_result.retrieval_context,
                        "tool_outputs": stream_result.tool_outputs,
                    }
                )
                continue

            await websocket.send_json(
                {"type": "error", "detail": f"Unsupported event type: {event_type or 'unknown'}"}
            )
    except WebSocketDisconnect:
        return


@router.websocket("/voice/ws")
async def support_voice_websocket(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> None:
    token = get_bearer_token_from_header(websocket.headers.get("authorization"))
    if not token:
        token = websocket.query_params.get("token")

    try:
        claims = get_access_claims_from_token(token or "")
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    settings = get_settings()
    stt = resolve_speech_to_text_provider(
        provider=settings.voice_stt_provider,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.voice_stt_gemini_model,
        whisper_api_key=settings.whisper_api_key,
        whisper_base_url=settings.whisper_base_url,
        whisper_model=settings.whisper_model,
        timeout_seconds=settings.voice_stt_timeout_seconds,
    )
    tts = resolve_text_to_speech_provider(
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

    service = get_support_agent_service()
    store = SupportChatStore()
    chat_id = websocket.query_params.get("chat_id")
    kb_user_id = websocket.query_params.get("kb_user_id")
    chat = await store.get_or_create_chat(
        db,
        tenant_id=claims["tenant_id"],
        user_id=claims["sub"],
        chat_id=chat_id,
    )
    chat_id = chat.id

    await websocket.accept()
    await websocket.send_json(
        {"type": "session.started", "protocol": "mcp.voice.v1", "session_id": chat_id, "chat_id": chat_id}
    )

    try:
        while True:
            payload = await websocket.receive_json()
            try:
                event = MCPClientEvent.model_validate(payload)
            except ValidationError:
                await websocket.send_json({"type": "error", "detail": "Invalid event payload."})
                continue

            event_type = event.type.strip()

            if event_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if event_type == "session.start":
                try:
                    start_event = MCPSessionStartIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid session.start payload."}
                    )
                    continue

                requested_chat_id = (start_event.session_id or "").strip() or None
                if requested_chat_id or chat_id:
                    chat = await store.get_or_create_chat(
                        db,
                        tenant_id=claims["tenant_id"],
                        user_id=claims["sub"],
                        chat_id=requested_chat_id or chat_id,
                    )
                    chat_id = chat.id

                if "kb_user_id" in payload:
                    kb_user_id = str(payload.get("kb_user_id") or "").strip() or None

                await websocket.send_json(
                    {
                        "type": "session.started",
                        "protocol": "mcp.voice.v1",
                        "session_id": chat_id,
                        "chat_id": chat_id,
                    }
                )
                continue

            if event_type == "session.stop":
                await websocket.send_json({"type": "session.stopped", "session_id": chat_id})
                await websocket.close(code=1000, reason="Session stopped by client")
                return

            if event_type == "input.text":
                try:
                    parsed_text = MCPInputTextIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid input.text payload."}
                    )
                    continue

                try:
                    stream_result = await service.stream_message(
                        db=db,
                        tenant_id=claims["tenant_id"],
                        user_id=claims["sub"],
                        message=parsed_text.text,
                        chat_id=chat_id,
                        kb_user_id=kb_user_id,
                    )
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue

                chat_id = stream_result.chat_id
                await websocket.send_json({"type": "response.started", "session_id": chat_id})

                chunks: list[str] = []
                async for chunk in stream_result.chunks:
                    chunks.append(chunk)
                    await websocket.send_json(
                        {"type": "llm.chunk", "session_id": chat_id, "content": chunk}
                    )
                    try:
                        audio_bytes = await tts.synthesize_chunk(text=chunk, session_id=chat_id)
                    except Exception:
                        audio_bytes = None
                    if audio_bytes:
                        await websocket.send_json(
                            {
                                "type": "tts.chunk",
                                "session_id": chat_id,
                                "mime_type": tts.mime_type,
                                "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        )

                await websocket.send_json(
                    {
                        "type": "response.done",
                        "session_id": chat_id,
                        "text": "".join(chunks).strip(),
                        "policy_blocked": stream_result.policy_blocked,
                        "retrieval_context": stream_result.retrieval_context,
                        "tool_outputs": stream_result.tool_outputs,
                    }
                )
                continue

            if event_type == "input.audio":
                try:
                    parsed_audio = MCPInputAudioIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid input.audio payload."}
                    )
                    continue

                try:
                    audio_bytes = base64.b64decode(parsed_audio.audio_b64, validate=True)
                except Exception:
                    await websocket.send_json({"type": "error", "detail": "Invalid base64 audio payload."})
                    continue

                try:
                    user_message = await stt.transcribe_audio(
                        audio_bytes=audio_bytes,
                        mime_type=parsed_audio.mime_type,
                        session_id=chat_id,
                    )
                except Exception:
                    await websocket.send_json({"type": "error", "detail": "Speech transcription failed."})
                    continue

                user_message = user_message.strip()
                if not user_message:
                    await websocket.send_json({"type": "error", "detail": "Speech transcription is empty."})
                    continue

                await websocket.send_json(
                    {"type": "stt.final", "session_id": chat_id, "text": user_message}
                )

                try:
                    stream_result = await service.stream_message(
                        db=db,
                        tenant_id=claims["tenant_id"],
                        user_id=claims["sub"],
                        message=user_message,
                        chat_id=chat_id,
                        kb_user_id=kb_user_id,
                    )
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "detail": str(exc)})
                    continue

                chat_id = stream_result.chat_id
                await websocket.send_json({"type": "response.started", "session_id": chat_id})

                chunks: list[str] = []
                async for chunk in stream_result.chunks:
                    chunks.append(chunk)
                    await websocket.send_json(
                        {"type": "llm.chunk", "session_id": chat_id, "content": chunk}
                    )
                    try:
                        audio_bytes = await tts.synthesize_chunk(text=chunk, session_id=chat_id)
                    except Exception:
                        audio_bytes = None
                    if audio_bytes:
                        await websocket.send_json(
                            {
                                "type": "tts.chunk",
                                "session_id": chat_id,
                                "mime_type": tts.mime_type,
                                "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        )

                await websocket.send_json(
                    {
                        "type": "response.done",
                        "session_id": chat_id,
                        "text": "".join(chunks).strip(),
                        "policy_blocked": stream_result.policy_blocked,
                        "retrieval_context": stream_result.retrieval_context,
                        "tool_outputs": stream_result.tool_outputs,
                    }
                )
                continue

            await websocket.send_json(
                {"type": "error", "detail": f"Unsupported event type: {event_type or 'unknown'}"}
            )
    except WebSocketDisconnect:
        return
