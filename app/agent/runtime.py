"""Production, request-scoped agent execution runtime.

This module composes the agent domain contracts and application ports. It has no
dependency on API frameworks, provider SDKs, vector stores, or experimental
orchestration code.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
from uuid import uuid4

from app.agent.context import ContextManager, ContextMessage
from app.agent.llm.chat_model import ChatMessage, ChatModel, ChatProviderError, GenerationOptions
from app.agent.models import (
    AgentError, AgentRequest, AgentResponse, Citation, ErrorCategory,
    ExecutionStatus, FinishReason, TokenUsage as ResponseTokenUsage,
)
from app.agent.persistence import ConversationNotFound, ConversationStore
from app.agent.retrieval_tool import RetrievalToolOutput
from app.agent.tools import (
    ToolAuthorizationError, ToolExecutionContext, ToolRegistry,
    ToolUnavailableError,
)


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    timeout_seconds: float = 120.0
    max_output_tokens: int = 2048
    max_execution_steps: int = 4
    max_history_messages: int = 30
    max_context_tokens: int = 12_000
    model_name: str | None = None
    system_prompt: str = "You are a helpful assistant. Use retrieved sources when provided. Cite them only with their supplied numbered references."

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.max_output_tokens < 1 or self.max_execution_steps < 1:
            raise ValueError("runtime limits must be positive")
        if self.max_history_messages < 0 or self.max_context_tokens < 1:
            raise ValueError("context limits are invalid")


@dataclass(frozen=True, slots=True)
class AuthenticatedUserContext:
    user_id: str
    claims: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.user_id.strip():
            raise ValueError("authenticated user id is required")


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    user: AuthenticatedUserContext
    session: Any
    request_id: str = field(default_factory=lambda: str(uuid4()))
    cancellation: asyncio.Event | None = None
    timeout_seconds: float | None = None


@dataclass(slots=True)
class AgentState:
    """Mutable state scoped to one call to AgentRuntime.execute."""

    request_id: str
    user: AuthenticatedUserContext
    conversation_id: str
    current_user_message: str
    authorized_knowledge_base_ids: tuple[str, ...] = ()
    history: list[ContextMessage] = field(default_factory=list)
    retrieved_context: str = ""
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    tool_executions: list[Any] = field(default_factory=list)
    model_response: Any | None = None
    citations: tuple[Citation, ...] = ()
    status: ExecutionStatus = ExecutionStatus.INCOMPLETE
    usage: ResponseTokenUsage | None = None
    user_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolIntent:
    name: str
    arguments: dict[str, Any]


class ToolDecision(Protocol):
    async def decide(self, *, message: str, history: Sequence[ContextMessage],
                     available_tools: Sequence[str]) -> Sequence[ToolIntent]: ...


class NoToolDecision:
    """Safe default: only explicitly configured retrieval is run automatically."""

    async def decide(self, *, message: str, history: Sequence[ContextMessage],
                     available_tools: Sequence[str]) -> Sequence[ToolIntent]:
        return ()


class ModelExecutor:
    def __init__(self, model: ChatModel) -> None:
        self.model = model

    async def generate(self, messages: Sequence[ContextMessage], request: AgentRequest,
                       config: RuntimeConfig):
        normalized = [ChatMessage(item.role, item.content) for item in messages]
        options = GenerationOptions(
            temperature=request.response.temperature,
            max_tokens=min(request.response.max_output_tokens or config.max_output_tokens,
                           config.max_output_tokens),
        )
        return await self.model.generate(normalized, options=options)


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def execute(self, intent: ToolIntent, *, context: ToolExecutionContext):
        # ToolRegistry validates schemas and authorization for every invocation.
        return await self.registry.execute(intent.name, intent.arguments, context=context)


class OutputProcessor:
    """Keep only citations backed by retrieval results and retain source details."""

    _citation_pattern = re.compile(r"\[(\d+)\]")

    def process(self, text: str, citations: Sequence[Citation]) -> tuple[str, tuple[Citation, ...]]:
        by_number = {citation.citation_number: citation for citation in citations}
        used: set[int] = set()

        def replace(match: re.Match[str]) -> str:
            number = int(match.group(1))
            if number not in by_number:
                return ""
            used.add(number)
            return match.group(0)

        output = self._citation_pattern.sub(replace, text).strip()
        return output, tuple(c for c in citations if c.citation_number in used)


class AgentRuntime:
    """Executes a single request with isolated state and bounded tool activity."""

    def __init__(self, *, model_executor: ModelExecutor, tool_executor: ToolExecutor,
                 conversations: ConversationStore, config: RuntimeConfig | None = None,
                 context_manager: ContextManager | None = None,
                 tool_decision: ToolDecision | None = None,
                 output_processor: OutputProcessor | None = None) -> None:
        self.model_executor = model_executor
        self.tool_executor = tool_executor
        self.conversations = conversations
        self.config = config or RuntimeConfig()
        self.context_manager = context_manager or ContextManager(
            max_tokens=self.config.max_context_tokens,
            max_history_messages=self.config.max_history_messages,
        )
        self.tool_decision = tool_decision or NoToolDecision()
        self.output_processor = output_processor or OutputProcessor()

    async def execute(self, request: AgentRequest, context: ExecutionContext) -> AgentResponse:
        conversation_id = request.conversation_id or ""
        timeout = context.timeout_seconds or self.config.timeout_seconds
        task = asyncio.create_task(self._execute(request, context))
        cancelled = asyncio.create_task(context.cancellation.wait()) if context.cancellation else None
        try:
            waiters = {task, cancelled} if cancelled else {task}
            done, _ = await asyncio.wait(waiters, timeout=timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                return self._error(context.request_id, conversation_id or "pending", "execution_timeout",
                                   ErrorCategory.TIMEOUT, True)
            if cancelled is not None and cancelled in done and context.cancellation.is_set():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError
            return await task
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception:
            return self._error(context.request_id, conversation_id or "pending", "agent_execution_failed",
                               ErrorCategory.INTERNAL, False)
        finally:
            if cancelled is not None:
                cancelled.cancel()

    async def _execute(self, request: AgentRequest, context: ExecutionContext) -> AgentResponse:
        holder: dict[str, AgentState] = {}
        try:
            return await self._execute_turn(request, context, holder)
        except asyncio.CancelledError:
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            return self._error(context.request_id, state.conversation_id if state else
                               (request.conversation_id or "pending"), "execution_timeout",
                               ErrorCategory.TIMEOUT, True)
        except _ExecutionLimitError:
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            return self._error(context.request_id, state.conversation_id if state else
                               (request.conversation_id or "pending"), "execution_limit_exceeded",
                               ErrorCategory.INTERNAL, False)
        except ToolAuthorizationError:
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            return self._error(context.request_id, state.conversation_id if state else
                               (request.conversation_id or "pending"), "tool_unauthorized",
                               ErrorCategory.AUTHORIZATION, False)
        except ConversationNotFound:
            return self._error(context.request_id, request.conversation_id or "pending",
                               "conversation_not_found", ErrorCategory.NOT_FOUND, False)
        except (ChatProviderError, ToolUnavailableError):
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            return self._error(context.request_id, state.conversation_id if state else
                               (request.conversation_id or "pending"), "execution_stage_failed",
                               ErrorCategory.PROVIDER, True)
        except Exception:
            state = holder.get("state")
            if state is not None and state.user_message_id:
                await self._safe_fail(state, context)
            return self._error(context.request_id, state.conversation_id if state else
                               (request.conversation_id or "pending"), "agent_execution_failed",
                               ErrorCategory.INTERNAL, False)

    async def _execute_turn(self, request: AgentRequest, context: ExecutionContext,
                            holder: dict[str, AgentState]) -> AgentResponse:
        if context.cancellation is not None and context.cancellation.is_set():
            raise asyncio.CancelledError
        if request.conversation_id is None:
            conversation = await self.conversations.create(
                context.session, user_id=context.user.user_id
            )
            conversation_id = str(conversation.id)
        else:
            conversation_id = request.conversation_id
            await self.conversations.get(context.session, user_id=context.user.user_id,
                                         conversation_id=conversation_id)
        state = AgentState(
            request_id=context.request_id, user=context.user,
            conversation_id=conversation_id, current_user_message=request.message,
            authorized_knowledge_base_ids=(request.knowledge_base_id,) if request.knowledge_base_id else (),
        )
        holder["state"] = state
        # Expose state only on this coroutine, never on the runtime instance.
        turn = await self.conversations.begin_turn(
            context.session, user_id=context.user.user_id, conversation_id=conversation_id,
            content=request.message, request_id=context.request_id,
        )
        state.user_message_id = str(turn.user_message.id)
        if turn.already_started:
            messages = await self.conversations.list_messages(
                context.session, user_id=context.user.user_id, conversation_id=conversation_id,
                limit=self.config.max_history_messages + 2,
            )
            existing_assistant = next((m for m in reversed(messages)
                                       if m.request_id == context.request_id and m.role == "assistant"), None)
            if existing_assistant:
                return AgentResponse(request_id=context.request_id, conversation_id=conversation_id,
                                     message_id=str(existing_assistant.id), status=ExecutionStatus.COMPLETED,
                                     assistant_output=existing_assistant.content, finish_reason=FinishReason.STOP)
        previous = await self.conversations.list_messages(
            context.session, user_id=context.user.user_id, conversation_id=conversation_id,
            limit=self.config.max_history_messages + 1,
        )
        state.history = [ContextMessage(m.role, m.content) for m in previous
                          if m.id != turn.user_message.id]

        citations: tuple[Citation, ...] = ()
        if request.knowledge_base_id:
            if "knowledge_base_retrieval" not in {t.name for t in self.tool_executor.registry.available_tools()}:
                raise ToolUnavailableError("retrieval tool is not registered")
            args: dict[str, Any] = {
                "query": request.message, "knowledge_base_id": request.knowledge_base_id,
                "top_k": request.retrieval.top_k,
                "document_ids": request.retrieval.document_ids,
                "strategy": request.retrieval.strategy,
                "score_threshold": request.retrieval.score_threshold,
            }
            result = await self.tool_executor.execute(
                ToolIntent("knowledge_base_retrieval", args),
                context=ToolExecutionContext(authenticated_user_id=context.user.user_id,
                                             session=context.session),
            )
            state.tool_executions.append(result.execution)
            if result.execution.status == "succeeded":
                retrieval = RetrievalToolOutput.model_validate(result.output)
                state.retrieved_context = retrieval.context.text
                citations = retrieval.citations
                state.citations = citations

        intents = await self.tool_decision.decide(
            message=request.message, history=state.history,
            available_tools=tuple(t.name for t in self.tool_executor.registry.available_tools()
                                  if t.name != "knowledge_base_retrieval"),
        )
        if len(intents) + len(state.tool_executions) > self.config.max_execution_steps:
            raise _ExecutionLimitError
        for intent in intents:
            if context.cancellation is not None and context.cancellation.is_set():
                raise asyncio.CancelledError
            result = await self.tool_executor.execute(
                intent, context=ToolExecutionContext(authenticated_user_id=context.user.user_id,
                                                     session=context.session),
            )
            state.tool_executions.append(result.execution)
            state.tool_results.append({"name": intent.name, "output": result.output,
                                       "status": result.execution.status})

        tool_context = "\n".join(
            f"Tool {item['name']} ({item['status']}): {item['output']}" for item in state.tool_results
        )
        prompt = self.context_manager.prepare(
            system_prompt=self.config.system_prompt,
            current_user_message=request.message,
            history=state.history,
            retrieved_context=state.retrieved_context,
            tool_results=[ContextMessage("system", tool_context)] if tool_context else (),
        )
        state.model_response = await self.model_executor.generate(prompt, request, self.config)
        output, citations = self.output_processor.process(state.model_response.content, state.citations)
        usage = state.model_response.usage
        state.usage = ResponseTokenUsage(
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
        ) if usage and any(v is not None for v in (usage.input_tokens, usage.output_tokens, usage.total_tokens)) else None
        assistant = await self.conversations.complete_turn(
            context.session, user_id=context.user.user_id, conversation_id=conversation_id,
            user_message_id=state.user_message_id, assistant_content=output,
        )
        state.status = ExecutionStatus.COMPLETED
        return AgentResponse(
            request_id=context.request_id, conversation_id=conversation_id, message_id=str(assistant.id),
            status=state.status, assistant_output=output, citations=citations,
            tool_executions=tuple(state.tool_executions), token_usage=state.usage,
            model=self.config.model_name or state.model_response.model, finish_reason=FinishReason.STOP,
        )

    async def _safe_fail(self, state: AgentState, context: ExecutionContext) -> None:
        try:
            await self.conversations.fail_turn(context.session, user_id=state.user.user_id,
                                               conversation_id=state.conversation_id,
                                               user_message_id=state.user_message_id or "")
        except Exception:
            return

    @staticmethod
    def _error(request_id: str, conversation_id: str, code: str, category: ErrorCategory,
               retryable: bool) -> AgentResponse:
        return AgentResponse(
            request_id=request_id, conversation_id=conversation_id, status=ExecutionStatus.FAILED,
            error=AgentError(code=code, message="The agent could not complete this request.",
                             category=category, retryable=retryable, request_reference=request_id),
            finish_reason=FinishReason.ERROR,
        )


class _ExecutionLimitError(RuntimeError):
    pass


class AgentOrchestrator:
    """Thin entry point for validating and dispatching one agent request."""

    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    async def run(self, request: AgentRequest, context: ExecutionContext) -> AgentResponse:
        return await self.runtime.execute(request, context)
