"""Public API schemas kept independent from provider SDK objects."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.contracts.session import AgentMessage
from dayu_agent.runtime.result import AgentResult
from dayu_agent.runtime.state import AgentRun, CancelDisposition


class HealthResponse(BaseModel):
    """Process liveness response."""

    status: str
    service: str
    version: str


class ReadyResponse(BaseModel):
    """Runtime, selected store, and provider readiness response."""

    status: str
    provider: str
    model: str
    detail: str
    components: dict[str, bool]


class SessionCreateRequest(BaseModel):
    """Optional metadata for creating a session."""

    model_config = ConfigDict(extra="forbid")

    user_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Created session identity and timestamps."""

    session_id: str
    user_id: str | None = None
    status: str = "open"
    version: int = 1
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SessionDetailResponse(SessionResponse):
    """Session metadata plus ordered conversation messages."""

    messages: tuple[AgentMessage, ...]


class ChatRequest(BaseModel):
    """Normalized user input for one Supervisor turn."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(max_length=50_000)
    session_id: str | None = Field(default=None, max_length=128)
    user_id: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(AgentResult):
    """Stable API representation of an AgentResult."""


class RunResponse(AgentRun):
    """Stable API representation of one durable AgentRun."""


class CancelResponse(BaseModel):
    """Return idempotent cancellation disposition and final run state."""

    disposition: CancelDisposition
    run: RunResponse


class ErrorResponse(BaseModel):
    """Uniform client-safe API error object."""

    error_code: str
    message: str
    request_id: str
    details: Any | None = None
