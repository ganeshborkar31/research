"""Provider and implementation independent tool contracts and registry."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.agent.models import ToolExecution


class AuthorizationRequirement(str, Enum):
    NONE = "none"
    AUTHENTICATED_USER = "authenticated_user"
    KNOWLEDGE_BASE_OWNER = "knowledge_base_owner"


@dataclass(frozen=True)
class ToolExecutionConfig:
    enabled: bool = True
    timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if isinstance(self.timeout_seconds, bool) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class ToolExecutionContext:
    """Trusted per-request identity and database scope created at the app boundary."""

    authenticated_user_id: str | None = None
    session: Any | None = None

    @classmethod
    def from_authenticated_claims(cls, claims: dict[str, Any], *, session: Any) -> "ToolExecutionContext":
        user_id = claims.get("sub")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ToolAuthorizationError("authenticated user identity is missing")
        return cls(authenticated_user_id=user_id.strip(), session=session)


class ToolAuthorizationError(PermissionError):
    code = "tool_unauthorized"


class ToolUnavailableError(LookupError):
    code = "tool_unavailable"


class ToolInputError(ValueError):
    code = "invalid_tool_input"


class AgentTool(Protocol):
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    authorization: AuthorizationRequirement
    config: ToolExecutionConfig

    async def authorize(self, context: ToolExecutionContext, arguments: BaseModel) -> None: ...

    async def execute(self, context: ToolExecutionContext, arguments: BaseModel) -> BaseModel | dict[str, Any]: ...


@dataclass(frozen=True)
class ToolResult:
    execution: ToolExecution
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RegisteredTool:
    tool: AgentTool
    config: ToolExecutionConfig


InputT = TypeVar("InputT", bound=BaseModel)


class ToolRegistry:
    """Register, authorize, validate, execute, and normalize tools."""

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(self, tool: AgentTool, *, config: ToolExecutionConfig | None = None) -> None:
        name = tool.name.strip()
        if not name or name != tool.name:
            raise ValueError("tool name must be non-empty and trimmed")
        if name in self._tools:
            raise ValueError(f"tool {name!r} is already registered")
        if not tool.description.strip():
            raise ValueError("tool description must be non-empty")
        if not issubclass(tool.input_schema, BaseModel) or not issubclass(tool.output_schema, BaseModel):
            raise TypeError("tool input_schema and output_schema must be Pydantic models")
        self._tools[name] = _RegisteredTool(tool, config or tool.config)

    def lookup(self, name: str) -> AgentTool:
        registered = self._tools.get(name)
        if registered is None:
            raise ToolUnavailableError(f"tool {name!r} is not registered")
        return registered.tool

    def configure(self, name: str, *, enabled: bool | None = None, timeout_seconds: float | None = None) -> None:
        registered = self._registered(name)
        registered.config = ToolExecutionConfig(
            enabled=registered.config.enabled if enabled is None else enabled,
            timeout_seconds=registered.config.timeout_seconds if timeout_seconds is None else timeout_seconds,
        )

    def available_tools(self) -> tuple[AgentTool, ...]:
        return tuple(item.tool for item in self._tools.values() if item.config.enabled)

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: ToolExecutionContext,
        tool_call_id: str | None = None,
    ) -> ToolResult:
        call_id = tool_call_id or str(uuid4())
        registered = self._registered(name)
        tool = registered.tool
        if not registered.config.enabled:
            raise ToolUnavailableError(f"tool {name!r} is disabled")
        try:
            parsed = tool.input_schema.model_validate(arguments)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ToolInputError("tool input does not match its schema") from exc
        if tool.authorization != AuthorizationRequirement.NONE:
            if not context.authenticated_user_id or not context.authenticated_user_id.strip():
                raise ToolAuthorizationError("authenticated user context is required")
            if tool.authorization == AuthorizationRequirement.KNOWLEDGE_BASE_OWNER and context.session is None:
                raise ToolAuthorizationError("database session is required for scope authorization")
        started = time.monotonic()
        try:
            await asyncio.wait_for(tool.authorize(context, parsed), timeout=registered.config.timeout_seconds)
        except asyncio.TimeoutError:
            return self._timed_out(call_id, name, started)
        except ToolAuthorizationError:
            raise
        except PermissionError as exc:
            raise ToolAuthorizationError("tool authorization failed") from exc
        except Exception:
            return self._failed(call_id, name, started, "tool_authorization_failed")

        remaining = registered.config.timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            return self._timed_out(call_id, name, started)
        try:
            raw = await asyncio.wait_for(
                tool.execute(context, parsed), timeout=remaining
            )
            normalized = tool.output_schema.model_validate(raw)
            output = normalized.model_dump(mode="json")
            execution = ToolExecution(
                tool_call_id=call_id, name=name, status="succeeded",
                duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            )
            return ToolResult(execution=execution, output=output)
        except asyncio.TimeoutError:
            return self._timed_out(call_id, name, started)
        except ToolAuthorizationError:
            raise
        except Exception:
            return self._failed(call_id, name, started, "tool_execution_failed")

    def _registered(self, name: str) -> _RegisteredTool:
        registered = self._tools.get(name)
        if registered is None:
            raise ToolUnavailableError(f"tool {name!r} is not registered")
        return registered

    @staticmethod
    def _timed_out(call_id: str, name: str, started: float) -> ToolResult:
        return ToolResult(execution=ToolExecution(
            tool_call_id=call_id, name=name, status="timed_out",
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            error_code="tool_timeout",
        ))

    @staticmethod
    def _failed(call_id: str, name: str, started: float, error_code: str) -> ToolResult:
        return ToolResult(execution=ToolExecution(
            tool_call_id=call_id, name=name, status="failed",
            duration_ms=max(0, round((time.monotonic() - started) * 1000)),
            error_code=error_code,
        ))
