from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.api.v1.dependencies import (
    get_access_claims_from_token,
    get_bearer_token_from_header,
    get_current_access_claims,
)
from app.schemas.mcp_voice import MCPAgentSetIn, MCPClientEvent, MCPInputAudioIn, MCPInputTextIn, MCPSessionStartIn
from app.schemas.voice_agent import VoiceAgentProfileOut
from app.services.voice_chat_service import get_voice_chat_service


router = APIRouter()


@router.get("/voice/agents", response_model=list[VoiceAgentProfileOut])
async def list_voice_agents(
    _claims: dict = Depends(get_current_access_claims),
) -> list[VoiceAgentProfileOut]:
    return get_voice_chat_service().list_agents()


@router.websocket("/voice/ws")
async def voice_mcp_websocket(
    websocket: WebSocket,
) -> None:
    token = get_bearer_token_from_header(websocket.headers.get("authorization"))
    if not token:
        token = websocket.query_params.get("token")

    try:
        claims = get_access_claims_from_token(token or "")
    except HTTPException:
        await websocket.close(code=4401, reason="Unauthorized")
        return

    service = get_voice_chat_service()
    session_id = websocket.query_params.get("session_id") or f"voice-{uuid4()}"
    user_id = claims["sub"]

    await websocket.accept()
    await websocket.send_json(_session_started_payload(session_id, service))

    try:
        while True:
            payload = await websocket.receive_json()
            try:
                event = MCPClientEvent.model_validate(payload)
            except ValidationError:
                await websocket.send_json({"type": "error", "detail": "Invalid MCP event payload."})
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

                if start_event.session_id:
                    session_id = start_event.session_id.strip()
                requested_agent_id = (start_event.agent_id or "").strip()
                if requested_agent_id:
                    async for event in service.process_mcp_event(
                        session_id=session_id,
                        user_id=user_id,
                        event={"type": "agent.set", "agent_id": requested_agent_id},
                    ):
                        await websocket.send_json(event)
                await websocket.send_json(_session_started_payload(session_id, service))
                continue

            if event_type == "session.stop":
                await websocket.send_json({"type": "session.stopped", "session_id": session_id})
                await websocket.close(code=1000, reason="Session stopped by client")
                return

            if event_type == "agent.set":
                try:
                    parsed_agent_set = MCPAgentSetIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid agent.set payload."}
                    )
                    continue
                event_payload = {
                    "type": "agent.set",
                    "agent_id": parsed_agent_set.agent_id,
                }
            elif event_type == "input.text":
                try:
                    parsed_text = MCPInputTextIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid input.text payload."}
                    )
                    continue
                event_payload = {
                    "type": "input.text",
                    "text": parsed_text.text,
                }
            elif event_type == "input.audio":
                try:
                    parsed_audio = MCPInputAudioIn.model_validate(payload)
                except ValidationError:
                    await websocket.send_json(
                        {"type": "error", "detail": "Invalid input.audio payload."}
                    )
                    continue
                event_payload = {
                    "type": "input.audio",
                    "audio_b64": parsed_audio.audio_b64,
                    "mime_type": parsed_audio.mime_type,
                }
            else:
                event_payload = payload

            async for event in service.process_mcp_event(
                session_id=session_id,
                user_id=user_id,
                event=event_payload,
            ):
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return


def _session_started_payload(session_id: str, service) -> dict:
    return {
        "type": "session.started",
        "protocol": "mcp.voice.v1",
        "session_id": session_id,
        "default_agent": service.get_default_agent().model_dump(),
    }
