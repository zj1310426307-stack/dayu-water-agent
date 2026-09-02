"""Provider-neutral session and committed-message value objects."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


class MessageRole(StrEnum):
    """Roles stored in committed conversation history."""

    USER = "user"
    ASSISTANT = "assistant"


class AgentMessage(BaseModel):
    """One immutable committed message with stable per-session ordering."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    run_id: str | None = None
    role: MessageRole
    content: str
    sequence: int = Field(default=1, ge=1)
    committed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SessionRecord(BaseModel):
    """Session metadata independent of the concrete storage backend."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str | None = None
    status: str = "open"
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
