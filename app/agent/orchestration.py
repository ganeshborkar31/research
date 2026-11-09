"""Dedicated agent turn orchestration, separate from experimental infra gateways."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from app.agent.models import (
    AgentError, AgentRequest, AgentResponse, Citation, ErrorCategory,
    ExecutionStatus, FinishReason, TokenUsage,
)
from app.agent.llm.chat_model import ChatMessage, ChatModel, ChatProviderError
from app.agent.llm.prompts import render_retrieval_chat


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    """Trusted per-turn context assembled by the application boundary."""

    request_id: str
    conversation_id: str
    system_prompt: str
    history: list[dict[str, str]] | None = None
    retrieval_context: str = ""
    citations: tuple[Citation, ...] = ()


class _TurnState(TypedDict, total=False):
    request: AgentRequest
    context: AgentTurnContext
    messages: list[ChatMessage]
    response: Any


class AgentOrchestrator:
    """Provider-independent agent graph for a single conversational turn.

    Retrieval and policy remain application-owned: callers pass the authorized
    conversation history, retrieval text, and citations in ``AgentTurnContext``.
    """

    def __init__(self, model: ChatModel) -> None:
        self.model = model
        from langgraph.graph import END, StateGraph

        graph = StateGraph(_TurnState)
        graph.add_node("render_prompt", self._render_prompt)
        graph.add_node("generate", self._generate)
        graph.set_entry_point("render_prompt")
        graph.add_edge("render_prompt", "generate")
        graph.add_edge("generate", END)
        self._graph = graph.compile()

    async def run(self, request: AgentRequest, context: AgentTurnContext) -> AgentResponse:
        try:
            result = await self._graph.ainvoke({"request": request, "context": context})
            generated = result["response"]
            usage = generated.usage
            return AgentResponse(
                request_id=context.request_id,
                conversation_id=context.conversation_id,
                status=ExecutionStatus.COMPLETED,
                assistant_output=generated.content,
                citations=context.citations,
                token_usage=TokenUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                ) if usage and any((usage.input_tokens is not None, usage.output_tokens is not None, usage.total_tokens is not None)) else None,
                model=generated.model,
                finish_reason=FinishReason.STOP,
            )
        except Exception as exc:
            category = ErrorCategory.TIMEOUT if isinstance(exc, TimeoutError) else ErrorCategory.PROVIDER
            return AgentResponse(
                request_id=context.request_id,
                conversation_id=context.conversation_id,
                status=ExecutionStatus.FAILED,
                error=AgentError(
                    code="agent_generation_failed",
                    message="The agent could not complete this request.",
                    category=category,
                    retryable=isinstance(exc, (TimeoutError, ChatProviderError)),
                    request_reference=context.request_id,
                ),
                finish_reason=FinishReason.ERROR,
            )

    def _render_prompt(self, state: _TurnState) -> _TurnState:
        request, context = state["request"], state["context"]
        messages = render_retrieval_chat(
            system_prompt=context.system_prompt,
            user_message=request.message,
            history=context.history,
            retrieval_context=context.retrieval_context,
        )
        return {"messages": messages}

    async def _generate(self, state: _TurnState) -> _TurnState:
        request: AgentRequest = state["request"]
        response = await self.model.generate(
            state["messages"],
            options=_generation_options(request),
        )
        return {"response": response}


def _generation_options(request: AgentRequest):
    from app.agent.llm.chat_model import GenerationOptions

    return GenerationOptions(
        temperature=request.response.temperature,
        max_tokens=request.response.max_output_tokens,
    )
