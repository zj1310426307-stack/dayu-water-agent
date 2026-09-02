"""Public API schemas kept independent from provider SDK objects."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.memory import AgentMessage
from dayu_agent.runtime.result import AgentResult


class HealthResponse(BaseModel):
    """Process liveness response."""

    status: str
    service: str
    version: str


class ReadyResponse(BaseModel):
    """Runtime and provider configuration readiness response."""

    status: str
    provider: str
    model: str
    detail: str


class SessionCreateRequest(BaseModel):
    """Optional metadata for creating a session."""

    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionResponse(BaseModel):
    """Created session identity and timestamps."""

    session_id: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SessionDetailResponse(SessionResponse):
    """Session metadata plus ordered conversation messages."""

    messages: tuple[AgentMessage, ...]


class ChatRequest(BaseModel):
    """Normalized user input for one Supervisor turn."""

    model_config = ConfigDict(extra="forbid")

    message: str
    session_id: str | None = None
    user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(AgentResult):
    """Stable API representation of an AgentResult."""


class ErrorResponse(BaseModel):
    """Uniform client-safe API error object."""

    error_code: str
    message: str
    request_id: str
    details: Any | None = None
