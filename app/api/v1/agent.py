"""Authenticated JSON and Fetch-compatible SSE endpoints for the production agent."""

import asyncio
import re
from contextlib import suppress
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import AgentRequest, AgentResponse
from app.agent.persistence import ConversationNotFound, ConversationStore
from app.agent.retrieval_tool import build_default_tool_registry
from app.agent.runtime import (AgentOrchestrator, AgentRuntime, AuthenticatedUserContext,
                               ExecutionContext, ModelExecutor, ToolExecutor)
from app.agent.llm.providers import build_chat_model
from app.api.v1.dependencies import get_current_access_claims
from app.core.config import get_settings
from app.db.session import get_db
from app.schemas.chat_api import ChatMessageOut, ChatSessionOut

router = APIRouter()


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    configured = getattr(request.app.state, "agent_orchestrator", None)
    if isinstance(configured, AgentOrchestrator):
        return configured
    try:
        model = build_chat_model(get_settings())
        runtime = AgentRuntime(model_executor=ModelExecutor(model),
                               tool_executor=ToolExecutor(build_default_tool_registry()),
                               conversations=ConversationStore())
        return AgentOrchestrator(runtime)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Agent runtime is unavailable.") from exc


@router.post("/turn", response_model=AgentResponse)
async def agent_turn(payload: AgentRequest, response: Response,
                     request_id: str | None = Header(default=None, alias="X-Request-ID"),
                     db: AsyncSession = Depends(get_db),
                     claims: dict = Depends(get_current_access_claims),
                     agent: AgentOrchestrator = Depends(get_agent_orchestrator)) -> AgentResponse:
    user_id = _authenticated_user_id(claims)
    trace_id = _request_id(request_id)
    response.headers["X-Request-ID"] = trace_id
    context = ExecutionContext(user=AuthenticatedUserContext(user_id, claims), session=db,
                               request_id=trace_id)
    return await agent.run(payload, context)


@router.post("/turn/stream", response_class=StreamingResponse,
             responses={200: {"description": "Versioned agent lifecycle events as Fetch-compatible SSE."}})
async def agent_turn_stream(payload: AgentRequest, request: Request,
                            request_id: str | None = Header(default=None, alias="X-Request-ID"),
                            db: AsyncSession = Depends(get_db),
                            claims: dict = Depends(get_current_access_claims),
                            agent: AgentOrchestrator = Depends(get_agent_orchestrator)):
    user_id = _authenticated_user_id(claims)
    trace_id = _request_id(request_id)
    cancellation = asyncio.Event()
    context = ExecutionContext(user=AuthenticatedUserContext(user_id, claims),
                               session=db, request_id=trace_id, cancellation=cancellation)

    async def watch_disconnect():
        while not cancellation.is_set():
            if await request.is_disconnected():
                cancellation.set()
                return
            await asyncio.sleep(0.25)

    async def events():
        watcher = asyncio.create_task(watch_disconnect())
        try:
            async for event in agent.runtime.stream(payload, context):
                if await request.is_disconnected():
                    cancellation.set()
                    break
                yield f"event: {event.event_type}\nid: {event.event_id}\ndata: {event.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            cancellation.set()
            raise
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):
                await watcher

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "X-Request-ID": trace_id})


@router.get("/conversations/{conversation_id}", response_model=ChatSessionOut)
async def get_agent_conversation(conversation_id: str,
                                db: AsyncSession = Depends(get_db),
                                claims: dict = Depends(get_current_access_claims)) -> ChatSessionOut:
    try:
        conversation = await _conversation_store().get(
            db, user_id=_authenticated_user_id(claims), conversation_id=conversation_id
        )
    except ConversationNotFound as exc:
        raise _not_found() from exc
    return ChatSessionOut.model_validate(conversation)


@router.get("/conversations/{conversation_id}/history", response_model=list[ChatMessageOut])
async def get_agent_history(conversation_id: str,
                            limit: int = Query(default=100, ge=1, le=200),
                            before_sequence: int | None = Query(default=None, ge=0),
                            db: AsyncSession = Depends(get_db),
                            claims: dict = Depends(get_current_access_claims)) -> list[ChatMessageOut]:
    try:
        messages = await _conversation_store().list_messages(
            db, user_id=_authenticated_user_id(claims), conversation_id=conversation_id,
            limit=limit, before_sequence=before_sequence,
        )
    except ConversationNotFound as exc:
        raise _not_found() from exc
    return [ChatMessageOut.model_validate(message) for message in messages]


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Archive an agent conversation")
async def archive_agent_conversation(conversation_id: str,
                                     db: AsyncSession = Depends(get_db),
                                     claims: dict = Depends(get_current_access_claims)) -> None:
    try:
        await _conversation_store().archive(
            db, user_id=_authenticated_user_id(claims), conversation_id=conversation_id
        )
    except ConversationNotFound as exc:
        raise _not_found() from exc


def _conversation_store() -> ConversationStore:
    return ConversationStore()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")


def _authenticated_user_id(claims: dict) -> str:
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Authenticated user identity is missing.",
                            headers={"WWW-Authenticate": "Bearer"})
    return user_id.strip()


def _request_id(value: str | None) -> str:
    # Accept a bounded, printable trace token; generate one if the header is malformed.
    if value and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        return value
    return str(uuid4())
