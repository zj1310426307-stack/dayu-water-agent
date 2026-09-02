"""Persistent AgentRun and durable stream state contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.exceptions import InvalidRunTransitionError
from dayu_agent.runtime.result import TokenUsage


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for immutable state records."""

    return datetime.now(UTC)


class RunStatus(StrEnum):
    """Complete and non-overlapping AgentRun lifecycle vocabulary."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_RUN_STATUSES = frozenset({RunStatus.PENDING, RunStatus.RUNNING})
TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
)
ALLOWED_RUN_TRANSITIONS = {
    RunStatus.PENDING: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
    ),
    RunStatus.RUNNING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
    ),
}


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    """Reject terminal rewrites and every transition absent from the frozen state machine."""

    if target not in ALLOWED_RUN_TRANSITIONS.get(current, frozenset()):
        raise InvalidRunTransitionError(
            details={"current_status": current.value, "target_status": target.value}
        )


class StreamEventType(StrEnum):
    """Transport-neutral event names persisted before SSE delivery."""

    RUN_STARTED = "run.started"
    RESPONSE_DELTA = "response.delta"
    RESPONSE_COMPLETED = "response.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"
    RUN_INTERRUPTED = "run.interrupted"


TERMINAL_EVENT_TYPES = frozenset(
    {
        StreamEventType.RESPONSE_COMPLETED,
        StreamEventType.RUN_FAILED,
        StreamEventType.RUN_CANCELLED,
        StreamEventType.RUN_INTERRUPTED,
    }
)


class AgentRun(BaseModel):
    """Provider-neutral, queryable record of one real execution attempt group."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    request_id: str
    trace_id: str
    session_id: str
    idempotency_key: str | None = None
    request_hash: str
    status: RunStatus = RunStatus.PENDING
    provider: str
    model: str
    attempt_count: int = Field(default=0, ge=0)
    worker_instance_id: str
    created_at: datetime = Field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    error_code: str | None = None
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunReservation(BaseModel):
    """Return an AgentRun and whether this caller owns its provider execution."""

    model_config = ConfigDict(frozen=True)

    run: AgentRun
    owns_execution: bool


class StreamEvent(BaseModel):
    """One durable, monotonically ordered event belonging to exactly one run."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    sequence: int = Field(ge=1)
    type: StreamEventType
    created_at: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class CancelDisposition(StrEnum):
    """Stable result of an idempotent cancellation request."""

    CANCELLED = "cancelled"
    ALREADY_CANCELLED = "already_cancelled"
    ALREADY_TERMINAL = "already_terminal"


class CancelResult(BaseModel):
    """Cancellation outcome returned by runtime and API boundaries."""

    model_config = ConfigDict(frozen=True)

    run: AgentRun
    disposition: CancelDisposition
