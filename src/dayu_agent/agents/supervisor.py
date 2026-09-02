"""Single Supervisor Agent with durable, cancellable execution ownership."""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from functools import partial
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from dayu_agent.contracts.session import MessageRole as SessionMessageRole
from dayu_agent.contracts.session import SessionRecord
from dayu_agent.exceptions import (
    DayuAgentError,
    GuardrailError,
    ProviderError,
    ProviderTimeoutError,
    RetryBudgetExhaustedError,
    RunCancelledError,
    RunError,
    RunInterruptedError,
    ToolValidationError,
)
from dayu_agent.guardrails import (
    InputGuardrail,
    NonEmptyInputGuardrail,
    NonEmptyOutputGuardrail,
    OutputGuardrail,
    PhaseZeroToolGuardrail,
    ToolGuardrail,
)
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
from dayu_agent.runtime.retry import RetryBudget
from dayu_agent.runtime.state import (
    TERMINAL_EVENT_TYPES,
    AgentRun,
    CancelResult,
    RunStatus,
    StreamEvent,
    StreamEventType,
)
from dayu_agent.runtime.store import RuntimeStore
from dayu_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentStreamEvent(BaseModel):
    """Compatibility view of one durable stream delta or final AgentResult."""

    model_config = ConfigDict(frozen=True)

    sequence: int | None = None
    run_id: str | None = None
    delta: str | None = None
    result: AgentResult | None = None
    done: bool = False


class SupervisorAgent:
    """Own exactly one Supervisor workflow and all run lifecycle transitions."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        session_store: RuntimeStore,
        tool_registry: ToolRegistry,
        retry_budget: RetryBudget | None = None,
        worker_instance_id: str | None = None,
        input_guardrails: tuple[InputGuardrail, ...] | None = None,
        output_guardrails: tuple[OutputGuardrail, ...] | None = None,
        tool_guardrails: tuple[ToolGuardrail, ...] | None = None,
    ) -> None:
        """Configure provider-neutral dependencies and process-local task ownership."""

        self.provider = provider
        self.session_store = session_store
        self.tool_registry = tool_registry
        self.retry_budget = retry_budget or RetryBudget()
        self.worker_instance_id = worker_instance_id or str(uuid4())
        self.input_guardrails = input_guardrails or (NonEmptyInputGuardrail(),)
        self.output_guardrails = output_guardrails or (NonEmptyOutputGuardrail(),)
        self.tool_guardrails = tool_guardrails or (PhaseZeroToolGuardrail(),)
        self.name = "SupervisorAgent"
        self._tasks: dict[str, asyncio.Task[AgentResult]] = {}
        self._accepting = True

    @property
    def accepting(self) -> bool:
        """Report whether this process accepts new execution ownership."""

        return self._accepting

    async def initialize(self) -> int:
        """Verify storage and reconcile active runs from prior process identities."""

        await self.session_store.initialize()
        interrupted = await self.session_store.reconcile_orphaned_runs(
            self.worker_instance_id
        )
        self._accepting = True
        return interrupted

    async def shutdown(self) -> int:
        """Stop admission, durably interrupt local runs, and cancel provider tasks."""

        self._accepting = False
        interrupted = await self.session_store.interrupt_worker_runs(self.worker_instance_id)
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return interrupted

    async def create_session(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        user_id: str | None = None,
    ) -> SessionRecord:
        """Expose session creation without leaking a concrete store."""

        return await self.session_store.create_session(metadata=metadata, user_id=user_id)

    async def get_run(self, run_id: str) -> AgentRun:
        """Return persistent run state through the runtime boundary."""

        return await self.session_store.get_run(run_id)

    async def cancel(self, run_id: str) -> CancelResult:
        """Commit cancellation before signalling the owned asyncio task."""

        outcome = await self.session_store.cancel_run(
            run_id, worker_instance_id=self.worker_instance_id
        )
        if outcome.run.status is RunStatus.CANCELLED:
            task = self._tasks.get(run_id)
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        return outcome

    async def _check_input(self, content: str, context: AgentContext) -> None:
        """Run input contracts before creating any persistent session or run state."""

        for guardrail in self.input_guardrails:
            decision = await guardrail.evaluate(content, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)

    async def _check_output(self, content: str, context: AgentContext) -> None:
        """Run output contracts before the atomic success commit."""

        for guardrail in self.output_guardrails:
            decision = await guardrail.evaluate(content, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)

    async def _provider_request(
        self, context: AgentContext, user_content: str
    ) -> ProviderRequest:
        """Translate committed history plus the uncommitted current input."""

        history = await self.session_store.list_messages(context.session_id)
        messages = [
            ProviderMessage(
                role=(
                    MessageRole.USER
                    if message.role is SessionMessageRole.USER
                    else MessageRole.ASSISTANT
                ),
                content=message.content,
            )
            for message in history
        ]
        messages.append(ProviderMessage(role=MessageRole.USER, content=user_content))
        return ProviderRequest(context=context, messages=tuple(messages))

    @staticmethod
    def _parse_tool_command(message: str) -> tuple[str, dict[str, Any]] | None:
        """Parse the explicit, permission-checked ``/tool name {json}`` interface."""

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
        """Execute one registered safe tool and normalize its audited result."""

        tool = self.tool_registry.get(name)
        for guardrail in self.tool_guardrails:
            decision = await guardrail.evaluate(tool, context)
            if not decision.allowed:
                raise GuardrailError(decision.reason)
        execution = await self.tool_registry.execute(name, payload)
        content = json.dumps(execution.output, ensure_ascii=False, separators=(",", ":"))
        await self._check_output(content, context)
        return AgentResult(
            request_id=context.request_id,
            run_id=context.run_id,
            trace_id=context.trace_id,
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
        idempotency_key: str | None = None,
    ) -> AgentResult:
        """Start or replay one durable non-streaming Supervisor run."""

        run = await self._start(
            message,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            request_id=request_id,
            idempotency_key=idempotency_key,
            streaming=False,
        )
        return await self._wait_for_result(run.id)

    async def start_stream(
        self,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AgentRun:
        """Start or replay a run and return its identity before SSE delivery."""

        return await self._start(
            message,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            request_id=request_id,
            idempotency_key=idempotency_key,
            streaming=True,
        )

    async def _start(
        self,
        message: str,
        *,
        session_id: str | None,
        user_id: str | None,
        metadata: dict[str, Any] | None,
        request_id: str | None,
        idempotency_key: str | None,
        streaming: bool,
    ) -> AgentRun:
        """Validate, reserve persistent ownership, and schedule provider work."""

        if not self._accepting:
            raise RunError("The runtime is shutting down and is not accepting new runs.")
        content = message.strip()
        safe_metadata = metadata or {}
        provisional = AgentContext(
            request_id=request_id or str(uuid4()),
            session_id=session_id or "unassigned",
            user_id=user_id,
            agent_name=self.name,
            metadata=safe_metadata,
        )
        await self._check_input(content, provisional)
        request_hash = self.request_hash(
            message=content,
            session_id=session_id,
            user_id=user_id,
            metadata=safe_metadata,
        )
        reservation = await self.session_store.reserve_run(
            session_id=session_id,
            session_metadata=safe_metadata,
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_id=provisional.request_id,
            trace_id=provisional.trace_id,
            provider=self.provider.name,
            model=self.provider.model_name,
            worker_instance_id=self.worker_instance_id,
            run_metadata={"streaming": streaming},
        )
        run = reservation.run
        if reservation.owns_execution:
            context = provisional.model_copy(
                update={"session_id": run.session_id, "run_id": run.id}
            )
            task = asyncio.create_task(
                self._execute_owned(
                    run,
                    context=context,
                    user_content=content,
                    user_metadata=safe_metadata,
                    streaming=streaming,
                ),
                name=f"dayu-run-{run.id}",
            )
            self._tasks[run.id] = task
            task.add_done_callback(partial(self._task_done, run.id))
        return run

    @staticmethod
    def request_hash(
        *,
        message: str,
        session_id: str | None,
        user_id: str | None,
        metadata: dict[str, Any],
    ) -> str:
        """Hash only canonical request semantics for idempotency comparison."""

        canonical = json.dumps(
            {
                "message": message,
                "metadata": metadata,
                "session_id": session_id,
                "user_id": user_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _task_done(self, run_id: str, task: asyncio.Task[AgentResult]) -> None:
        """Remove local ownership and consume background exceptions safely."""

        self._tasks.pop(run_id, None)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.info(
                "Agent run task ended with a controlled failure",
                extra={"run_id": run_id, "error": type(exception).__name__},
            )

    async def _execute_owned(
        self,
        run: AgentRun,
        *,
        context: AgentContext,
        user_content: str,
        user_metadata: dict[str, Any],
        streaming: bool,
    ) -> AgentResult:
        """Own all provider, retry, terminal-state, and success-commit work."""

        try:
            await self.session_store.mark_run_running(run.id, self.worker_instance_id)
            with TraceContext(
                context,
                provider=self.provider.name,
                model=self.provider.model_name,
            ) as trace:
                tool_command = self._parse_tool_command(user_content)
                if tool_command is not None:
                    trace.annotate(tool_name=tool_command[0])
                    result = await self._run_tool(*tool_command, context)
                else:
                    request = await self._provider_request(context, user_content)
                    response = await self._run_provider_with_retry(
                        run.id, request, streaming=streaming
                    )
                    await self._check_output(response.content, context)
                    result = self._result_from_provider(context, response)
                await self.session_store.commit_run_success(
                    run.id,
                    user_content=user_content,
                    user_metadata=user_metadata,
                    assistant_content=result.content,
                    assistant_metadata={
                        "provider": self.provider.name,
                        "model": self.provider.model_name,
                    },
                    result=result,
                )
                return result
        except asyncio.CancelledError:
            await self._ensure_cancelled_if_active(run.id)
            raise
        except DayuAgentError as exc:
            await self.session_store.fail_run(run.id, error_code=exc.error_code)
            raise
        except Exception as exc:
            wrapped = ProviderError(
                details={"provider": self.provider.name, "model": self.provider.model_name}
            )
            await self.session_store.fail_run(run.id, error_code=wrapped.error_code)
            raise wrapped from exc

    async def _ensure_cancelled_if_active(self, run_id: str) -> None:
        """Make direct task cancellation durable unless shutdown already interrupted it."""

        try:
            await self.session_store.cancel_run(
                run_id, worker_instance_id=self.worker_instance_id
            )
        except DayuAgentError:
            logger.info(
                "Run cancellation raced with another terminal transition",
                extra={"run_id": run_id},
            )

    async def _run_provider_with_retry(
        self,
        run_id: str,
        request: ProviderRequest,
        *,
        streaming: bool,
    ) -> ProviderResponse:
        """Apply the sole retry budget, never replaying after a streamed delta."""

        started = monotonic()
        while True:
            durable = await self.session_store.increment_run_attempt(run_id)
            emitted_delta = False
            try:
                remaining = self.retry_budget.max_elapsed_seconds - (monotonic() - started)
                if remaining <= 0:
                    raise RetryBudgetExhaustedError(
                        details={"attempt_count": durable.attempt_count}
                    )
                async with asyncio.timeout(remaining):
                    if not streaming:
                        return await self.provider.run(request)
                    final_response: ProviderResponse | None = None
                    async for event in self.provider.stream(request):
                        if event.delta is not None:
                            emitted_delta = True
                            await self.session_store.append_stream_event(
                                run_id,
                                StreamEventType.RESPONSE_DELTA,
                                {"delta": event.delta},
                            )
                        if event.done:
                            final_response = event.response
                    if final_response is None:
                        raise ProviderError(
                            "The provider stream ended without a final response."
                        )
                    return final_response
            except TimeoutError as exc:
                error: ProviderError = ProviderTimeoutError(
                    details={"provider": self.provider.name, "model": self.provider.model_name}
                )
                if emitted_delta:
                    raise error from exc
            except ProviderError as exc:
                error = exc
                if emitted_delta or not exc.retryable:
                    raise

            elapsed = monotonic() - started
            delay = self.retry_budget.delay_after(durable.attempt_count)
            if not self.retry_budget.permits_retry(
                failed_attempt=durable.attempt_count,
                elapsed=elapsed,
                delay=delay,
            ):
                raise RetryBudgetExhaustedError(
                    details={
                        "attempt_count": durable.attempt_count,
                        "elapsed_seconds": round(elapsed, 3),
                        "last_error": error.error_code,
                    }
                ) from error
            logger.warning(
                "Retrying provider attempt within runtime budget",
                extra={
                    "request_id": request.context.request_id,
                    "run_id": run_id,
                    "trace_id": request.context.trace_id,
                    "session_id": request.context.session_id,
                    "agent_name": self.name,
                    "provider": self.provider.name,
                    "model": self.provider.model_name,
                    "attempt": durable.attempt_count,
                    "retry_delay": round(delay, 3),
                    "error": error.error_code,
                },
            )
            await asyncio.sleep(delay)

    def _result_from_provider(
        self,
        context: AgentContext,
        response: ProviderResponse,
    ) -> AgentResult:
        """Convert a provider response to the stable public result schema."""

        return AgentResult(
            request_id=context.request_id,
            run_id=context.run_id,
            trace_id=context.trace_id,
            session_id=context.session_id,
            agent=self.name,
            content=response.content,
            status=AgentStatus.SUCCESS,
            usage=response.usage,
            warnings=(),
            metadata={"provider": self.provider.name, "model": self.provider.model_name},
        )

    async def _wait_for_result(self, run_id: str) -> AgentResult:
        """Await local work or poll the durable state for an idempotent replay."""

        task = self._tasks.get(run_id)
        if task is not None:
            return await asyncio.shield(task)
        while True:
            run = await self.session_store.get_run(run_id)
            if run.status is RunStatus.COMPLETED and run.result is not None:
                return AgentResult.model_validate(run.result)
            if run.status is RunStatus.CANCELLED:
                raise RunCancelledError(details={"run_id": run.id})
            if run.status is RunStatus.INTERRUPTED:
                raise RunInterruptedError(details={"run_id": run.id})
            if run.status is RunStatus.FAILED:
                raise RunError(
                    "The idempotent agent run previously failed.",
                    details={"run_id": run.id, "error_code": run.error_code},
                )
            await asyncio.sleep(0.05)

    async def stream_run(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        poll_interval: float = 0.05,
    ) -> AsyncIterator[StreamEvent]:
        """Replay then tail durable events until exactly one terminal event."""

        await self.session_store.get_run(run_id)
        cursor = after_sequence
        while True:
            events = await self.session_store.list_stream_events(
                run_id, after_sequence=cursor
            )
            for event in events:
                cursor = event.sequence
                yield event
                if event.type in TERMINAL_EVENT_TYPES:
                    return
            run = await self.session_store.get_run(run_id)
            if run.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.INTERRUPTED,
            }:
                return
            await asyncio.sleep(poll_interval)

    async def stream(
        self,
        message: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Expose a backward-compatible view backed entirely by durable events."""

        run = await self.start_stream(
            message,
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        async for event in self.stream_run(run.id):
            if event.type is StreamEventType.RESPONSE_DELTA:
                yield AgentStreamEvent(
                    sequence=event.sequence,
                    run_id=run.id,
                    delta=str(event.payload.get("delta", "")),
                )
            elif event.type is StreamEventType.RESPONSE_COMPLETED:
                result = AgentResult.model_validate(event.payload["result"])
                yield AgentStreamEvent(
                    sequence=event.sequence,
                    run_id=run.id,
                    result=result,
                    done=True,
                )
