from fastapi import APIRouter, Depends, Request, Response
from urllib.parse import parse_qs

from app.api.v1.dependencies import get_current_access_claims
from app.domain.live_chat import LiveChatRequest, LiveChatResponse
from app.infra.voice import build_twiml_gather_response
from app.services.live_chat import get_live_chat_service


router = APIRouter(dependencies=[Depends(get_current_access_claims)])


@router.post("/respond", response_model=LiveChatResponse)
async def live_chat_respond(
    payload: LiveChatRequest,
) -> LiveChatResponse:
    service = get_live_chat_service()
    return await service.respond(payload)


@router.post("/twilio/voice", response_class=Response)
async def twilio_voice_webhook(
    request: Request,
) -> Response:
    service = get_live_chat_service()
    body = (await request.body()).decode("utf-8")
    form = parse_qs(body)
    call_sid = (form.get("CallSid", ["unknown-call"])[0] or "unknown-call").strip()
    caller = (form.get("From", [""])[0] or "").strip()
    speech_result = (form.get("SpeechResult", [""])[0] or "").strip()

    if not speech_result:
        twiml = build_twiml_gather_response(
            message="Hello, please tell me how I can help you today.",
            action_url=str(request.url),
        )
        return Response(content=twiml, media_type="application/xml")

    reply = await service.respond_voice(
        call_sid=call_sid,
        speech_text=speech_result,
        caller_id=caller or None,
    )

    twiml = build_twiml_gather_response(
        message=reply,
        action_url=str(request.url),
    )
    return Response(content=twiml, media_type="application/xml")
