"""Single Supervisor Agent that owns Phase-00 runtime orchestration."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from dayu_agent.exceptions import GuardrailError, ProviderError, ToolValidationError
from dayu_agent.guardrails import (
    InputGuardrail,
    NonEmptyInputGuardrail,
    NonEmptyOutputGuardrail,
    OutputGuardrail,
    PhaseZeroToolGuardrail,
    ToolGuardrail,
)
from dayu_agent.memory import MessageRole as SessionMessageRole
from dayu_agent.memory import SessionRecord, SessionStore
from dayu_agent.observability import TraceContext
from dayu_agent.providers.base import (
    MessageRole,
    ModelProvider,
    ProviderMessage,
    ProviderRequest,
    ProviderResponse,
)
from dayu_agent.runtime.context import AgentContext
from dayu_agent.runtime.result import AgentResult, AgentStatus, ToolCallRecord
from dayu_agent.tools.registry import ToolRegistry


class AgentStreamEvent(BaseModel):
    """Normalized stream delta or final AgentResult."""

    model_config = ConfigDict(frozen=True)

    delta: str | None = None
    result: AgentResult | None = None
    done: bool = False


class SupervisorAgent:
    """Coordinate input checks, sessions, provider calls, tools, and tracing."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        session_store: SessionStore,
        tool_registry: ToolRegistry,
        input_guardrails: tuple[InputGuardrail, ...] | None = None,
        output_guardrails: tuple[OutputGuardrail, ...] | None = None,
        tool_guardrails: tuple[ToolGuardrail, ...] | None = None,
    ) -> None:
        self.provider = provider
        self.session_store = session_store
        self.tool_registry = tool_registry
        self.input_guardrails = input_guardrails or (NonEmptyInputGuardrail(),)
        self.output_guardrails = output_guardrails or (NonEmptyOutputGuardrail(),)
        self.tool_guardrails = tool_guardrails or (PhaseZeroToolGuardrail(),)
        self.name = "SupervisorAgent"

    async def create_session(self, metadata: dict[str, Any] | None = None) -> SessionRecord:
        """Expose session creation without leaking a concrete store."""

        return await self.session_store.create_session(metadata=metadata)

    async def _resolve_session(
        self,
        session_id: str | None,
        metadata: dict[str, Any],
    ) -> SessionRecord:
        """Create an implicit session or require an explicit session to exist."""

        if session_id is None:
            return await self.session_store.create_session(metadata=metadata)
        return await self.session_store.get_session(session_id)

    async def _check_input(self, content: str, context: AgentContext) -> None:
        """Run all input contracts before persisting user data."""

        for guardrail in self.input_guardrails:
            decision = await guardrail.evaluate(content, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)

    async def _check_output(self, content: str, context: AgentContext) -> None:
        """Run all output contracts before persisting assistant data."""

        for guardrail in self.output_guardrails:
            decision = await guardrail.evaluate(content, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)

    async def _provider_request(self, context: AgentContext) -> ProviderRequest:
        """Translate stored messages to provider-neutral history."""

        history = await self.session_store.list_messages(context.session_id)
        messages = tuple(
            ProviderMessage(
                role=(
                    MessageRole.USER
                    if message.role is SessionMessageRole.USER
                    else MessageRole.ASSISTANT
                ),
                content=message.content,
            )
            for message in history
        )
        return ProviderRequest(context=context, messages=messages)

    def _parse_tool_command(self, message: str) -> tuple[str, dict[str, Any]] | None:
        """Parse the explicit, permission-checked ``/tool name {json}`` proof interface."""

        if message == "/tool":
            raise ToolValidationError("Tool command must include a registered tool name.")
        if not message.startswith("/tool "):
            return None
        parts = message.split(maxsplit=2)
        if len(parts) < 2:
            raise ToolValidationError("Tool command must include a registered tool name.")
        payload_text = parts[2] if len(parts) == 3 else "{}"
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise ToolValidationError("Tool command payload must be a JSON object.") from exc
        if not isinstance(payload, dict):
            raise ToolValidationError("Tool command payload must be a JSON object.")
        return parts[1], payload

    async def _run_tool(
        self,
        name: str,
        payload: dict[str, Any],
        context: AgentContext,
    ) -> AgentResult:
        """Execute a registered safe tool after all tool guardrails allow it."""

        tool = self.tool_registry.get(name)
        for guardrail in self.tool_guardrails:
            decision = await guardrail.evaluate(tool, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)
        execution = await self.tool_registry.execute(name, payload)
        content = json.dumps(execution.output, ensure_ascii=False, separators=(",", ":"))
        await self._check_output(content, context)
        await self.session_store.append_message(
            context.session_id,
            role=SessionMessageRole.ASSISTANT,
            content=content,
            metadata={"tool_name": name},
        )
        return AgentResult(
            request_id=context.request_id,
            session_id=context.session_id,
            agent=self.name,
            content=content,
            status=AgentStatus.SUCCESS,
            tool_calls=(
                ToolCallRecord(
                    name=name,
                    status=AgentStatus.SUCCESS,
                    duration_ms=execution.duration_ms,
                    output=execution.output,
                ),
            ),
            metadata={"provider": self.provider.name, "model": self.provider.model_name},
        )

    async def run(
        self,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AgentResult:
        """Execute one complete Supervisor turn and persist both sides on success."""

        safe_metadata = metadata or {}
        provisional_context = AgentContext(
            request_id=request_id or str(uuid4()),
            session_id=session_id or "unassigned",
            user_id=user_id,
            agent_name=self.name,
            metadata=safe_metadata,
        )
        await self._check_input(message, provisional_context)
        session = await self._resolve_session(session_id, safe_metadata)
        context = provisional_context.model_copy(update={"session_id": session.id})
        await self.session_store.append_message(
            session.id,
            role=SessionMessageRole.USER,
            content=message.strip(),
            metadata=safe_metadata,
        )

        with TraceContext(
            context,
            provider=self.provider.name,
            model=self.provider.model_name,
        ) as trace:
            tool_command = self._parse_tool_command(message.strip())
            if tool_command is not None:
                trace.annotate(tool_name=tool_command[0])
                return await self._run_tool(*tool_command, context)

            response = await self.provider.run(await self._provider_request(context))
            await self._check_output(response.content, context)
            await self.session_store.append_message(
                session.id,
                role=SessionMessageRole.ASSISTANT,
                content=response.content,
                metadata={"provider": self.provider.name, "model": self.provider.model_name},
            )
            return self._result_from_provider(context, response)

    def _result_from_provider(
        self,
        context: AgentContext,
        response: ProviderResponse,
    ) -> AgentResult:
        """Convert a provider response to the public stable result schema."""

        return AgentResult(
            request_id=context.request_id,
            session_id=context.session_id,
            agent=self.name,
            content=response.content,
            status=AgentStatus.SUCCESS,
            usage=response.usage,
            warnings=(),
            metadata={"provider": self.provider.name, "model": self.provider.model_name},
        )

    async def stream(
        self,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Forward native provider deltas, then persist and yield one final result."""

        if message.strip().startswith("/tool "):
            result = await self.run(
                message,
                session_id=session_id,
                user_id=user_id,
                metadata=metadata,
                request_id=request_id,
            )
            yield AgentStreamEvent(result=result, done=True)
            return

        safe_metadata = metadata or {}
        provisional_context = AgentContext(
            request_id=request_id or str(uuid4()),
            session_id=session_id or "unassigned",
            user_id=user_id,
            agent_name=self.name,
            metadata=safe_metadata,
        )
        await self._check_input(message, provisional_context)
        session = await self._resolve_session(session_id, safe_metadata)
        context = provisional_context.model_copy(update={"session_id": session.id})
        await self.session_store.append_message(
            session.id,
            role=SessionMessageRole.USER,
            content=message.strip(),
            metadata=safe_metadata,
        )

        final_response: ProviderResponse | None = None
        with TraceContext(
            context,
            provider=self.provider.name,
            model=self.provider.model_name,
        ):
            async for event in self.provider.stream(await self._provider_request(context)):
                if event.delta is not None:
                    yield AgentStreamEvent(delta=event.delta)
                if event.done:
                    final_response = event.response

            if final_response is None:
                raise ProviderError("The provider stream ended without a final response.")
            await self._check_output(final_response.content, context)
            await self.session_store.append_message(
                session.id,
                role=SessionMessageRole.ASSISTANT,
                content=final_response.content,
                metadata={"provider": self.provider.name, "model": self.provider.model_name},
            )
            yield AgentStreamEvent(
                result=self._result_from_provider(context, final_response),
                done=True,
            )
