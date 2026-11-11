"""Authenticated JSON and Fetch-compatible SSE endpoints for the production agent."""

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.models import AgentRequest
from app.agent.persistence import ConversationStore
from app.agent.retrieval_tool import build_default_tool_registry
from app.agent.runtime import (AgentOrchestrator, AgentRuntime, AuthenticatedUserContext,
                               ExecutionContext, ModelExecutor, ToolExecutor)
from app.agent.llm.providers import build_chat_model
from app.api.v1.dependencies import get_current_access_claims
from app.core.config import get_settings
from app.db.session import get_db

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


@router.post("/turn")
async def agent_turn(payload: AgentRequest, db: AsyncSession = Depends(get_db),
                     claims: dict = Depends(get_current_access_claims),
                     agent: AgentOrchestrator = Depends(get_agent_orchestrator)):
    context = ExecutionContext(user=AuthenticatedUserContext(claims["sub"], claims),
                               session=db, request_id=str(uuid4()))
    return await agent.run(payload, context)


@router.post("/turn/stream")
async def agent_turn_stream(payload: AgentRequest, request: Request,
                            db: AsyncSession = Depends(get_db),
                            claims: dict = Depends(get_current_access_claims),
                            agent: AgentOrchestrator = Depends(get_agent_orchestrator)):
    cancellation = asyncio.Event()
    context = ExecutionContext(user=AuthenticatedUserContext(claims["sub"], claims),
                               session=db, request_id=str(uuid4()), cancellation=cancellation)

    async def events():
        try:
            async for event in agent.runtime.stream(payload, context):
                if await request.is_disconnected():
                    cancellation.set()
                    break
                yield f"event: {event.event_type}\nid: {event.event_id}\ndata: {event.model_dump_json()}\n\n"
        except asyncio.CancelledError:
            cancellation.set()
            raise

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
