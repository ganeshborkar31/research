from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.infra.llm import GeminiClient, LangChainChatGateway, LangGraphChatGateway
from app.schemas.support_agent import SupportChatResponse
from app.schemas.user_memory import UserHistoryEventIn
from app.services.support_chat_store import SupportChatStore
from app.services.support_policy_registry import SupportPolicy, resolve_support_policy
from app.services.user_vector_store import UserVectorStore


logger = logging.getLogger(__name__)


class _SupportState(TypedDict, total=False):
    tenant_id: str
    user_id: str
    message: str
    history: list[dict[str, str]]
    policy: SupportPolicy
    retrieval_context: str
    reply: str
    policy_blocked: bool
    tool_outputs: dict[str, Any]


@dataclass(frozen=True)
class SupportStreamResult:
    chat_id: str
    policy_blocked: bool
    retrieval_context: str | None
    tool_outputs: dict[str, Any] | None
    chunks: AsyncIterator[str]


class SupportAgentService:
    def __init__(
        self,
        *,
        runtime: LangChainChatGateway | LangGraphChatGateway | None = None,
        vector_store: UserVectorStore | None = None,
    ) -> None:
        settings = get_settings()
        if runtime is None:
            gemini_client = GeminiClient(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
            )
            if settings.use_langgraph_chat and LangGraphChatGateway:
                runtime = LangGraphChatGateway(gemini_client=gemini_client)
            else:
                if settings.use_langgraph_chat and not LangGraphChatGateway:
                    logger.warning("use_langgraph_chat=true, but langgraph is unavailable; using LangChain.")
                runtime = LangChainChatGateway(gemini_client=gemini_client)

        self._runtime = runtime
        self._vector_store = vector_store or UserVectorStore()
        self._store = SupportChatStore()
        self._graph = _build_support_graph(self._run_policy_guard, self._retrieve_context, self._generate_reply)

    async def reply(
        self,
        *,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        message: str,
        chat_id: str | None = None,
        kb_user_id: str | None = None,
    ) -> SupportChatResponse:
        chat = await self._store.get_or_create_chat(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        history_messages = await self._store.list_messages(
            db,
            chat_id=chat.id,
            tenant_id=tenant_id,
            user_id=user_id,
            limit=30,
        )
        history = [{"role": m.role, "content": m.content} for m in history_messages]
        payload: _SupportState = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "message": message,
            "history": history,
        }
        if kb_user_id:
            payload["tool_outputs"] = {"kb_user_id": kb_user_id}
        final_state = await self._graph.ainvoke(payload)
        reply_text = str(final_state.get("reply", "")).strip()
        await self._store.append_messages(
            db,
            chat=chat,
            user_content=str(message).strip(),
            assistant_content=reply_text,
        )
        await self._store_history_event(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=chat.id,
            message=str(message).strip(),
            role="user",
        )
        await self._store_history_event(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=chat.id,
            message=reply_text,
            role="assistant",
        )
        return SupportChatResponse(
            chat_id=chat.id,
            reply=str(final_state.get("reply", "")).strip(),
            policy_blocked=bool(final_state.get("policy_blocked", False)),
            retrieval_context=final_state.get("retrieval_context"),
            tool_outputs=final_state.get("tool_outputs"),
        )

    async def stream_message(
        self,
        *,
        db: AsyncSession,
        tenant_id: str,
        user_id: str,
        message: str,
        chat_id: str | None = None,
        kb_user_id: str | None = None,
    ) -> SupportStreamResult:
        user_content = message.strip()
        if not user_content:
            raise ValueError("Message cannot be empty.")

        chat = await self._store.get_or_create_chat(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
        )
        history_messages = await self._store.list_messages(
            db,
            chat_id=chat.id,
            tenant_id=tenant_id,
            user_id=user_id,
            limit=30,
        )
        history = [{"role": m.role, "content": m.content} for m in history_messages]

        payload: _SupportState = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "message": user_content,
            "history": history,
        }
        if kb_user_id:
            payload["tool_outputs"] = {"kb_user_id": kb_user_id}

        policy_state = await self._run_policy_guard(payload)
        policy = policy_state.get("policy") or resolve_support_policy(tenant_id or "default")
        if policy_state.get("policy_blocked"):
            reply_text = str(policy_state.get("reply", "")).strip()
            await self._store.append_messages(
                db,
                chat=chat,
                user_content=user_content,
                assistant_content=reply_text,
            )
            await self._store_history_event(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=chat.id,
                message=user_content,
                role="user",
            )
            await self._store_history_event(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=chat.id,
                message=reply_text,
                role="assistant",
            )

            async def _blocked_chunks() -> AsyncIterator[str]:
                if reply_text:
                    yield reply_text

            return SupportStreamResult(
                chat_id=chat.id,
                policy_blocked=True,
                retrieval_context=None,
                tool_outputs=policy_state.get("tool_outputs"),
                chunks=_blocked_chunks(),
            )

        payload.update(policy_state)
        retrieval_state = await self._retrieve_context(payload)
        payload.update(retrieval_state)
        retrieval_context_value = str(payload.get("retrieval_context", "")).strip() or None
        tool_outputs = payload.get("tool_outputs")

        async def _stream_chunks() -> AsyncIterator[str]:
            chunks: list[str] = []
            async for chunk in self._runtime.stream_reply(
                system_prompt=policy.system_prompt(),
                user_message=user_content,
                history=history,
                retrieval_context=retrieval_context_value or "No retrieval context available.",
            ):
                if not chunk:
                    continue
                chunks.append(chunk)
                yield chunk

            assistant_reply = "".join(chunks).strip()
            await self._store.append_messages(
                db,
                chat=chat,
                user_content=user_content,
                assistant_content=assistant_reply,
            )
            await self._store_history_event(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=chat.id,
                message=user_content,
                role="user",
            )
            await self._store_history_event(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=chat.id,
                message=assistant_reply,
                role="assistant",
            )

        return SupportStreamResult(
            chat_id=chat.id,
            policy_blocked=False,
            retrieval_context=retrieval_context_value,
            tool_outputs=tool_outputs,
            chunks=_stream_chunks(),
        )

    async def _run_policy_guard(self, state: _SupportState) -> _SupportState:
        tenant_id = str(state.get("tenant_id", "")).strip()
        policy = resolve_support_policy(tenant_id or "default")
        message = str(state.get("message", "")).lower()
        blocked_terms = [term.lower() for term in policy.blocked_terms]
        if any(term and term in message for term in blocked_terms):
            return {
                "policy": policy,
                "policy_blocked": True,
                "reply": policy.escalation_message,
            }
        return {"policy": policy, "policy_blocked": False}

    async def _retrieve_context(self, state: _SupportState) -> _SupportState:
        policy: SupportPolicy = state.get("policy") or resolve_support_policy("default")
        message = str(state.get("message", "")).strip()
        tenant_id = str(state.get("tenant_id", "")).strip()
        user_id = str(state.get("user_id", "")).strip()
        tool_outputs = dict(state.get("tool_outputs") or {})
        kb_user_id = tool_outputs.get("kb_user_id") or policy.kb_user_id
        if not kb_user_id:
            kb_user_id = get_settings().support_kb_user_fallback

        @tool("kb_search", return_direct=True)
        async def kb_search(query: str) -> str:
            """Search the support knowledge base for relevant guidance."""
            return await self._build_retrieval_context(
                tenant_id=tenant_id,
                user_id=kb_user_id,
                query=query,
            )

        try:
            retrieval_context = await kb_search.ainvoke({"query": message})
        except Exception as exc:
            logger.warning("Support KB retrieval failed for tenant_id=%s: %s", tenant_id, exc)
            retrieval_context = "No retrieval context available."

        tool_outputs["kb_user_id"] = kb_user_id
        tool_outputs["kb_search"] = "ok" if retrieval_context else "empty"
        return {"retrieval_context": retrieval_context, "tool_outputs": tool_outputs}

    async def _generate_reply(self, state: _SupportState) -> _SupportState:
        policy: SupportPolicy = state.get("policy") or resolve_support_policy("default")
        history = state.get("history", []) or []
        retrieval_context = str(state.get("retrieval_context", "")).strip()
        message = str(state.get("message", "")).strip()
        reply = await self._runtime.generate_reply(
            system_prompt=policy.system_prompt(),
            user_message=message,
            history=history,
            retrieval_context=retrieval_context or "No retrieval context available.",
        )
        return {"reply": reply}

    async def _build_retrieval_context(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
    ) -> str:
        try:
            doc_hits = await self._vector_store.search_user_documents(
                tenant_id=tenant_id,
                user_id=user_id,
                query=query,
                include_global=True,
                limit=3,
            )
        except Exception as exc:
            logger.warning(
                "Qdrant retrieval unavailable for tenant_id=%s user_id=%s: %s",
                tenant_id,
                user_id,
                exc,
            )
            return "No retrieval context available."

        snippets = [f"[doc] {hit.content}" for hit in doc_hits if hit.content]
        if not snippets:
            return "No retrieval context available."
        return "\n".join(snippets)

    async def _store_history_event(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        message: str,
        role: str,
    ) -> None:
        try:
            await self._vector_store.store_history_event(
                UserHistoryEventIn(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    message=message,
                    role=role,
                    metadata={"channel": "support"},
                )
            )
        except Exception as exc:
            logger.warning(
                "Failed to index support history in Qdrant for tenant_id=%s user_id=%s session_id=%s: %s",
                tenant_id,
                user_id,
                session_id,
                exc,
            )


def _build_support_graph(policy_node, retrieval_node, generate_node):
    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - optional fallback
        logger.warning("LangGraph unavailable, using sequential fallback: %s", exc)

        class _FallbackGraph:
            async def ainvoke(self, state):
                state.update(await policy_node(state))
                if state.get("policy_blocked"):
                    return state
                state.update(await retrieval_node(state))
                state.update(await generate_node(state))
                return state

        return _FallbackGraph()

    builder = StateGraph(_SupportState)
    builder.add_node("policy_guard", policy_node)
    builder.add_node("retrieve_context", retrieval_node)
    builder.add_node("generate_reply", generate_node)
    builder.set_entry_point("policy_guard")
    builder.add_conditional_edges(
        "policy_guard",
        _policy_route,
        {"blocked": END, "ok": "retrieve_context"},
    )
    builder.add_edge("retrieve_context", "generate_reply")
    builder.add_edge("generate_reply", END)
    return builder.compile()


def _policy_route(state: _SupportState) -> str:
    if state.get("policy_blocked"):
        return "blocked"
    return "ok"


_support_agent_service: SupportAgentService | None = None


def get_support_agent_service() -> SupportAgentService:
    global _support_agent_service
    if _support_agent_service is None:
        _support_agent_service = SupportAgentService()
    return _support_agent_service
