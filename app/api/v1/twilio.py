from __future__ import annotations

from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.v1.dependencies import get_current_access_claims
from app.core.config import get_settings
from app.services.twilio_service import (
    TwilioConfigurationError,
    TwilioRequestError,
    get_twilio_service,
)
from app.services.voice_chat_service import get_voice_chat_service


router = APIRouter()


class TwilioSMSOutRequest(BaseModel):
    to: str = Field(min_length=3)
    message: str = Field(min_length=1)
    from_number: str | None = None


class TwilioCallOutRequest(BaseModel):
    to: str = Field(min_length=3)
    welcome_message: str | None = None
    from_number: str | None = None


class TwilioOutboundResponse(BaseModel):
    sid: str | None = None
    status: str | None = None
    to: str | None = None
    from_number: str | None = Field(default=None, alias="from")
    direction: str | None = None

    model_config = {
        "populate_by_name": True,
    }


@router.post("/sms/send", response_model=TwilioOutboundResponse)
async def send_sms_outbound(
    payload: TwilioSMSOutRequest,
    _claims: dict = Depends(get_current_access_claims),
) -> TwilioOutboundResponse:
    service = get_twilio_service()
    try:
        result = await service.send_sms(
            to_number=payload.to,
            body=payload.message,
            from_number=payload.from_number,
        )
    except (TwilioConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TwilioRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return TwilioOutboundResponse.model_validate(result)


@router.post("/voice/call", response_model=TwilioOutboundResponse)
async def start_outbound_call(
    payload: TwilioCallOutRequest,
    request: Request,
    _claims: dict = Depends(get_current_access_claims),
) -> TwilioOutboundResponse:
    service = get_twilio_service()
    session_id = f"outbound-{uuid4()}"
    settings = get_settings()

    base_url = (settings.twilio_public_base_url or "").strip().rstrip("/")
    if base_url:
        query_params: dict[str, str] = {"session_id": session_id}
        if payload.welcome_message:
            query_params["welcome_message"] = payload.welcome_message
        twiml_url = (
            f"{base_url}/api/v1/twilio/voice/outbound/twiml?"
            f"{urlencode(query_params)}"
        )
    else:
        twiml_url = request.url_for("twilio_outbound_call_twiml")
        twiml_url = str(twiml_url.include_query_params(session_id=session_id))
        if payload.welcome_message:
            twiml_url = str(request.url_for("twilio_outbound_call_twiml").include_query_params(
                session_id=session_id,
                welcome_message=payload.welcome_message,
            ))

    try:
        result = await service.create_call(
            to_number=payload.to,
            twiml_url=twiml_url,
            from_number=payload.from_number,
        )
    except (TwilioConfigurationError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TwilioRequestError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return TwilioOutboundResponse.model_validate(result)


@router.post("/voice/incoming", name="twilio_voice_incoming")
async def twilio_voice_incoming(
    request: Request,
    CallSid: str = Form(default=""),
) -> Response:
    service = get_twilio_service()
    call_sid = CallSid.strip() or f"call-{uuid4()}"
    action_url = str(request.url_for("twilio_voice_respond").include_query_params(session_id=call_sid))

    twiml = service.voice_gather_twiml(
        say_text="Welcome to Knowledge AI support. How can I help you today?",
        action_url=action_url,
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/voice/respond", name="twilio_voice_respond")
async def twilio_voice_respond(
    request: Request,
    session_id: str | None = None,
    SpeechResult: str = Form(default=""),
    Digits: str = Form(default=""),
    CallSid: str = Form(default=""),
    From: str = Form(default=""),
) -> Response:
    service = get_twilio_service()
    voice = get_voice_chat_service()

    sid = (session_id or CallSid or f"call-{uuid4()}").strip()
    user_id = (From or "twilio-caller").strip()
    message = (SpeechResult or Digits).strip()

    if not message:
        action_url = str(request.url_for("twilio_voice_respond").include_query_params(session_id=sid))
        twiml = service.voice_gather_twiml(
            say_text="I did not catch that. Please say your question again.",
            action_url=action_url,
        )
        return Response(content=twiml, media_type="application/xml")

    reply_text = "I am sorry, I could not generate a response right now."
    async for event in voice.process_mcp_event(
        session_id=sid,
        user_id=user_id,
        event={"type": "input.text", "text": message},
    ):
        if event.get("type") == "response.done" and event.get("text"):
            reply_text = str(event["text"]).strip()

    action_url = str(request.url_for("twilio_voice_respond").include_query_params(session_id=sid))
    twiml = service.voice_gather_twiml(say_text=reply_text, action_url=action_url)
    return Response(content=twiml, media_type="application/xml")


@router.api_route("/voice/outbound/twiml", methods=["GET", "POST"], name="twilio_outbound_call_twiml")
async def twilio_outbound_call_twiml(
    request: Request,
    session_id: str,
    welcome_message: str | None = None,
) -> Response:
    service = get_twilio_service()
    action_url = str(request.url_for("twilio_voice_respond").include_query_params(session_id=session_id))
    twiml = service.voice_gather_twiml(
        say_text=welcome_message or "Hello, this is Knowledge AI support. How can I help you?",
        action_url=action_url,
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/sms/incoming")
async def twilio_sms_incoming(
    Body: str = Form(default=""),
    From: str = Form(default=""),
) -> Response:
    service = get_twilio_service()
    voice = get_voice_chat_service()

    sender = (From or "twilio-sms").strip()
    message = Body.strip()

    if not message:
        twiml = service.sms_twiml(message="Please send a non-empty message.")
        return Response(content=twiml, media_type="application/xml")

    session_id = f"sms-{sender or 'anon'}"
    reply_text = "Thanks. We got your message."
    async for event in voice.process_mcp_event(
        session_id=session_id,
        user_id=sender or None,
        event={"type": "input.text", "text": message},
    ):
        if event.get("type") == "response.done" and event.get("text"):
            reply_text = str(event["text"]).strip()

    twiml = service.sms_twiml(message=reply_text)
    return Response(content=twiml, media_type="application/xml")
