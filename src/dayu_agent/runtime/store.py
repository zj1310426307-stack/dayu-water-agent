"""Abstract persistence boundary shared by Supervisor and infrastructure adapters."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from dayu_agent.contracts.session import AgentMessage, MessageRole, SessionRecord
from dayu_agent.runtime.result import AgentResult
from dayu_agent.runtime.state import (
    AgentRun,
    CancelResult,
    RunReservation,
    StreamEvent,
    StreamEventType,
)


class SessionStore(ABC):
    """Session contract retained from Phase-00 without persistence leakage."""

    @abstractmethod
    async def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionRecord:
        """Create a new session and reject duplicate identifiers."""

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord:
        """Return a session or raise a stable not-found error."""

    @abstractmethod
    async def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Append one committed compatibility message with a stable sequence."""

    @abstractmethod
    async def list_messages(self, session_id: str) -> tuple[AgentMessage, ...]:
        """Return committed messages in stable sequence order."""

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """Remove committed history while retaining session identity."""


class RuntimeStore(SessionStore):
    """Atomic persistence contract for sessions, runs, commits, and stream events."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize connections without creating or migrating production tables."""

    @abstractmethod
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
        """Resolve idempotency, session ownership, and one-active-run atomically."""

    @abstractmethod
    async def get_run(self, run_id: str) -> AgentRun:
        """Return one persistent run."""

    @abstractmethod
    async def mark_run_running(self, run_id: str, worker_instance_id: str) -> AgentRun:
        """Transition a pending run to running and persist its first event."""

    @abstractmethod
    async def increment_run_attempt(self, run_id: str) -> AgentRun:
        """Persist an attempt before invoking the external provider."""

    @abstractmethod
    async def append_stream_event(
        self,
        run_id: str,
        event_type: StreamEventType,
        payload: dict[str, Any],
    ) -> StreamEvent:
        """Append one event with a per-run monotonic sequence."""

    @abstractmethod
    async def list_stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[StreamEvent, ...]:
        """Return durable events strictly after the resume cursor."""

    @abstractmethod
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
        """Atomically commit both messages, final result, usage, and completion event."""

    @abstractmethod
    async def fail_run(self, run_id: str, *, error_code: str) -> AgentRun:
        """Atomically publish a failed terminal state and event."""

    @abstractmethod
    async def cancel_run(self, run_id: str, *, worker_instance_id: str) -> CancelResult:
        """Atomically cancel a locally owned active run or report terminal state."""

    @abstractmethod
    async def interrupt_worker_runs(self, worker_instance_id: str) -> int:
        """Interrupt active runs owned by a process during graceful shutdown."""

    @abstractmethod
    async def reconcile_orphaned_runs(self, worker_instance_id: str) -> int:
        """Mark active runs from prior process instances as interrupted."""

    @abstractmethod
    async def prune_stream_events(self, *, before: datetime) -> int:
        """Delete expired events belonging only to terminal runs."""

    @abstractmethod
    async def ping(self) -> bool:
        """Return whether the selected store dependency is reachable."""

    @abstractmethod
    async def close(self) -> None:
        """Release store-owned resources."""
