"""Concurrency-safe in-memory reference implementation of RuntimeStore."""

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

from dayu_agent.contracts.session import AgentMessage, MessageRole, SessionRecord, utc_now
from dayu_agent.exceptions import (
    CancellationUnavailableError,
    IdempotencyConflictError,
    RunNotFoundError,
    SessionBusyError,
    SessionError,
    SessionNotFoundError,
)
from dayu_agent.runtime.result import AgentResult
from dayu_agent.runtime.state import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    AgentRun,
    CancelResult,
    RunReservation,
    RunStatus,
    StreamEvent,
    StreamEventType,
    validate_run_transition,
)
from dayu_agent.runtime.store import RuntimeStore


class InMemorySessionStore(RuntimeStore):
    """Process-local RuntimeStore used for deterministic development and unit tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[AgentMessage]] = {}
        self._runs: dict[str, AgentRun] = {}
        self._events: dict[str, list[StreamEvent]] = {}
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._ready = True

    async def initialize(self) -> None:
        """Mark the dependency-free store ready."""

        self._ready = True

    async def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionRecord:
        """Create a session atomically and return a defensive copy."""

        record = SessionRecord(
            id=session_id or str(uuid4()),
            user_id=user_id,
            metadata=metadata if metadata is not None else {},
        )
        async with self._lock:
            if record.id in self._sessions:
                raise SessionError(
                    "A session with this identifier already exists.",
                    details={"session_id": record.id},
                )
            self._sessions[record.id] = record
            self._messages[record.id] = []
        return record.model_copy(deep=True)

    async def get_session(self, session_id: str) -> SessionRecord:
        """Read one session while keeping internal state private."""

        async with self._lock:
            return self._session_locked(session_id).model_copy(deep=True)

    def _session_locked(self, session_id: str) -> SessionRecord:
        """Return an existing session while the caller owns the store lock."""

        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFoundError(details={"session_id": session_id})
        return record

    def _active_run_locked(self, session_id: str) -> AgentRun | None:
        """Return the active run for one session while holding the store lock."""

        return next(
            (
                run
                for run in self._runs.values()
                if run.session_id == session_id and run.status in ACTIVE_RUN_STATUSES
            ),
            None,
        )

    async def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Append a compatibility message only when no run owns the session."""

        async with self._lock:
            record = self._session_locked(session_id)
            active = self._active_run_locked(session_id)
            if active is not None:
                raise SessionBusyError(details={"session_id": session_id, "run_id": active.id})
            message = AgentMessage(
                session_id=session_id,
                role=role,
                content=content,
                sequence=len(self._messages[session_id]) + 1,
                metadata=metadata if metadata is not None else {},
            )
            self._messages[session_id].append(message)
            self._sessions[session_id] = record.model_copy(
                update={"updated_at": utc_now(), "version": record.version + 1}
            )
            return message.model_copy(deep=True)

    async def list_messages(self, session_id: str) -> tuple[AgentMessage, ...]:
        """Return defensive copies ordered by their stable sequence."""

        async with self._lock:
            self._session_locked(session_id)
            return tuple(item.model_copy(deep=True) for item in self._messages[session_id])

    async def clear_session(self, session_id: str) -> None:
        """Clear committed history only when no run owns the session."""

        async with self._lock:
            record = self._session_locked(session_id)
            active = self._active_run_locked(session_id)
            if active is not None:
                raise SessionBusyError(details={"session_id": session_id, "run_id": active.id})
            self._messages[session_id].clear()
            self._sessions[session_id] = record.model_copy(
                update={"updated_at": utc_now(), "version": record.version + 1}
            )

    async def reserve_run(
        self,
        *,
        session_id: str | None,
        session_metadata: dict[str, Any],
        user_id: str | None,
        idempotency_key: str | None,
        request_hash: str,
        request_id: str,
        trace_id: str,
        provider: str,
        model: str,
        worker_instance_id: str,
        run_metadata: dict[str, Any],
    ) -> RunReservation:
        """Reserve exactly one active run after idempotency and session checks."""

        async with self._lock:
            if idempotency_key is not None and idempotency_key in self._idempotency:
                existing = self._runs[self._idempotency[idempotency_key]]
                if existing.request_hash != request_hash:
                    raise IdempotencyConflictError(
                        details={"idempotency_key": idempotency_key, "run_id": existing.id}
                    )
                return RunReservation(run=existing.model_copy(deep=True), owns_execution=False)

            if session_id is None:
                session = SessionRecord(user_id=user_id, metadata=session_metadata)
                self._sessions[session.id] = session
                self._messages[session.id] = []
            else:
                session = self._session_locked(session_id)

            active = self._active_run_locked(session.id)
            if active is not None:
                raise SessionBusyError(details={"session_id": session.id, "run_id": active.id})

            run = AgentRun(
                request_id=request_id,
                trace_id=trace_id,
                session_id=session.id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                provider=provider,
                model=model,
                worker_instance_id=worker_instance_id,
                metadata=run_metadata,
            )
            self._runs[run.id] = run
            self._events[run.id] = []
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = run.id
            return RunReservation(run=run.model_copy(deep=True), owns_execution=True)

    async def get_run(self, run_id: str) -> AgentRun:
        """Return a defensive copy of one run."""

        async with self._lock:
            return self._run_locked(run_id).model_copy(deep=True)

    def _run_locked(self, run_id: str) -> AgentRun:
        """Return an existing run while the caller owns the store lock."""

        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(details={"run_id": run_id})
        return run

    def _append_event_locked(
        self,
        run_id: str,
        event_type: StreamEventType,
        payload: dict[str, Any],
    ) -> StreamEvent:
        """Append a monotonically ordered event while holding the store lock."""

        event = StreamEvent(
            run_id=run_id,
            sequence=len(self._events[run_id]) + 1,
            type=event_type,
            payload=payload,
        )
        self._events[run_id].append(event)
        return event

    async def mark_run_running(self, run_id: str, worker_instance_id: str) -> AgentRun:
        """Transition pending to running and publish run.started atomically."""

        async with self._lock:
            run = self._run_locked(run_id)
            validate_run_transition(run.status, RunStatus.RUNNING)
            updated = run.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "started_at": utc_now(),
                    "worker_instance_id": worker_instance_id,
                }
            )
            self._runs[run_id] = updated
            self._append_event_locked(
                run_id,
                StreamEventType.RUN_STARTED,
                {"status": RunStatus.RUNNING.value},
            )
            return updated.model_copy(deep=True)

    async def increment_run_attempt(self, run_id: str) -> AgentRun:
        """Increment the durable provider-attempt counter."""

        async with self._lock:
            run = self._run_locked(run_id)
            if run.status is not RunStatus.RUNNING:
                validate_run_transition(run.status, RunStatus.RUNNING)
            updated = run.model_copy(update={"attempt_count": run.attempt_count + 1})
            self._runs[run_id] = updated
            return updated.model_copy(deep=True)

    async def append_stream_event(
        self,
        run_id: str,
        event_type: StreamEventType,
        payload: dict[str, Any],
    ) -> StreamEvent:
        """Append a durable event after confirming the run exists."""

        async with self._lock:
            self._run_locked(run_id)
            return self._append_event_locked(run_id, event_type, payload).model_copy(deep=True)

    async def list_stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[StreamEvent, ...]:
        """Return events strictly after a non-negative resume cursor."""

        async with self._lock:
            self._run_locked(run_id)
            return tuple(
                event.model_copy(deep=True)
                for event in self._events[run_id]
                if event.sequence > after_sequence
            )

    async def commit_run_success(
        self,
        run_id: str,
        *,
        user_content: str,
        user_metadata: dict[str, Any],
        assistant_content: str,
        assistant_metadata: dict[str, Any],
        result: AgentResult,
    ) -> AgentRun:
        """Commit a complete turn and completed state under one lock."""

        async with self._lock:
            run = self._run_locked(run_id)
            validate_run_transition(run.status, RunStatus.COMPLETED)
            session = self._session_locked(run.session_id)
            sequence = len(self._messages[run.session_id]) + 1
            self._messages[run.session_id].extend(
                [
                    AgentMessage(
                        session_id=run.session_id,
                        run_id=run.id,
                        role=MessageRole.USER,
                        content=user_content,
                        sequence=sequence,
                        metadata=user_metadata,
                    ),
                    AgentMessage(
                        session_id=run.session_id,
                        run_id=run.id,
                        role=MessageRole.ASSISTANT,
                        content=assistant_content,
                        sequence=sequence + 1,
                        metadata=assistant_metadata,
                    ),
                ]
            )
            now = utc_now()
            self._sessions[run.session_id] = session.model_copy(
                update={"updated_at": now, "version": session.version + 1}
            )
            updated = run.model_copy(
                update={
                    "status": RunStatus.COMPLETED,
                    "completed_at": now,
                    "usage": result.usage,
                    "result": result.model_dump(mode="json"),
                    "error_code": None,
                }
            )
            self._runs[run_id] = updated
            self._append_event_locked(
                run_id,
                StreamEventType.RESPONSE_COMPLETED,
                {"result": result.model_dump(mode="json")},
            )
            return updated.model_copy(deep=True)

    async def fail_run(self, run_id: str, *, error_code: str) -> AgentRun:
        """Publish failed exactly once without committing conversation messages."""

        async with self._lock:
            run = self._run_locked(run_id)
            if run.status in TERMINAL_RUN_STATUSES:
                return run.model_copy(deep=True)
            validate_run_transition(run.status, RunStatus.FAILED)
            updated = run.model_copy(
                update={
                    "status": RunStatus.FAILED,
                    "completed_at": utc_now(),
                    "error_code": error_code,
                }
            )
            self._runs[run_id] = updated
            self._append_event_locked(
                run_id,
                StreamEventType.RUN_FAILED,
                {"status": RunStatus.FAILED.value, "error_code": error_code},
            )
            return updated.model_copy(deep=True)

    async def cancel_run(self, run_id: str, *, worker_instance_id: str) -> CancelResult:
        """Cancel an active locally owned run with idempotent terminal behavior."""

        async with self._lock:
            run = self._run_locked(run_id)
            if run.status is RunStatus.CANCELLED:
                return CancelResult(run=run.model_copy(deep=True), disposition="already_cancelled")
            if run.status in TERMINAL_RUN_STATUSES:
                return CancelResult(run=run.model_copy(deep=True), disposition="already_terminal")
            if run.worker_instance_id != worker_instance_id:
                raise CancellationUnavailableError(
                    details={"run_id": run.id, "worker_instance_id": run.worker_instance_id}
                )
            validate_run_transition(run.status, RunStatus.CANCELLED)
            updated = run.model_copy(
                update={"status": RunStatus.CANCELLED, "completed_at": utc_now()}
            )
            self._runs[run_id] = updated
            self._append_event_locked(
                run_id,
                StreamEventType.RUN_CANCELLED,
                {"status": RunStatus.CANCELLED.value},
            )
            return CancelResult(run=updated.model_copy(deep=True), disposition="cancelled")

    def _interrupt_locked(self, run: AgentRun) -> None:
        """Publish interrupted state for one active run while holding the lock."""

        validate_run_transition(run.status, RunStatus.INTERRUPTED)
        self._runs[run.id] = run.model_copy(
            update={"status": RunStatus.INTERRUPTED, "completed_at": utc_now()}
        )
        self._append_event_locked(
            run.id,
            StreamEventType.RUN_INTERRUPTED,
            {"status": RunStatus.INTERRUPTED.value},
        )

    async def interrupt_worker_runs(self, worker_instance_id: str) -> int:
        """Interrupt all active runs owned by the shutting-down process."""

        async with self._lock:
            runs = [
                run
                for run in self._runs.values()
                if run.status in ACTIVE_RUN_STATUSES
                and run.worker_instance_id == worker_instance_id
            ]
            for run in runs:
                self._interrupt_locked(run)
            return len(runs)

    async def reconcile_orphaned_runs(self, worker_instance_id: str) -> int:
        """Interrupt active runs that belong to an earlier process identity."""

        async with self._lock:
            runs = [
                run
                for run in self._runs.values()
                if run.status in ACTIVE_RUN_STATUSES
                and run.worker_instance_id != worker_instance_id
            ]
            for run in runs:
                self._interrupt_locked(run)
            return len(runs)

    async def prune_stream_events(self, *, before: datetime) -> int:
        """Prune only expired events for terminal runs."""

        async with self._lock:
            removed = 0
            for run_id, events in self._events.items():
                if self._runs[run_id].status not in TERMINAL_RUN_STATUSES:
                    continue
                retained = [event for event in events if event.created_at >= before]
                removed += len(events) - len(retained)
                self._events[run_id] = retained
            return removed

    async def ping(self) -> bool:
        """Return the explicit in-memory readiness flag."""

        return self._ready

    async def close(self) -> None:
        """Mark the process-local store closed without discarding caller-owned data."""

        self._ready = False
