"""Production construction for the application agent runtime."""

from fastapi import HTTPException, Request

from app.agent.llm.providers import build_chat_model
from app.agent.persistence import ConversationStore
from app.agent.retrieval_tool import build_default_tool_registry
from app.agent.runtime import AgentOrchestrator, AgentRuntime, ModelExecutor, ToolExecutor
from app.core.config import get_settings


def get_agent_orchestrator(request: Request) -> AgentOrchestrator:
    configured = getattr(request.app.state, "agent_orchestrator", None)
    if isinstance(configured, AgentOrchestrator):
        return configured
    try:
        model = build_chat_model(get_settings())
        runtime = AgentRuntime(
            model_executor=ModelExecutor(model),
            tool_executor=ToolExecutor(build_default_tool_registry()),
            conversations=ConversationStore(),
        )
        return AgentOrchestrator(runtime)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Agent runtime is unavailable.") from exc
