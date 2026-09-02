"""Session contracts and the default in-memory implementation."""

import asyncio
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.exceptions import SessionError, SessionNotFoundError


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(UTC)


class MessageRole(StrEnum):
    """Roles stored in the local conversation history."""

    USER = "user"
    ASSISTANT = "assistant"


class AgentMessage(BaseModel):
    """One immutable message in a session."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: MessageRole
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class SessionRecord(BaseModel):
    """Session metadata independent of the concrete storage backend."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SessionStore(ABC):
    """Persistence boundary required by the Supervisor runtime."""

    @abstractmethod
    async def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> SessionRecord:
        """Create a new session and reject duplicate identifiers."""

    @abstractmethod
    async def get_session(self, session_id: str) -> SessionRecord:
        """Return a session or raise ``SessionNotFoundError``."""

    @abstractmethod
    async def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Append one validated message and update the session timestamp."""

    @abstractmethod
    async def list_messages(self, session_id: str) -> tuple[AgentMessage, ...]:
        """Return messages in insertion order."""

    @abstractmethod
    async def clear_session(self, session_id: str) -> None:
        """Remove messages while retaining the session identity."""


class InMemorySessionStore(SessionStore):
    """Concurrency-safe process-local session store for Phase-00 and tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._messages: dict[str, list[AgentMessage]] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> SessionRecord:
        """Create a session atomically and return a defensive copy."""

        record = SessionRecord(id=session_id or str(uuid4()), metadata=metadata or {})
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
        """Read one session while keeping internal mutable state private."""

        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(details={"session_id": session_id})
            return record.model_copy(deep=True)

    async def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Append a message atomically after verifying the session exists."""

        message = AgentMessage(role=role, content=content, metadata=metadata or {})
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(details={"session_id": session_id})
            self._messages[session_id].append(message)
            self._sessions[session_id] = record.model_copy(update={"updated_at": utc_now()})
        return message.model_copy(deep=True)

    async def list_messages(self, session_id: str) -> tuple[AgentMessage, ...]:
        """Return immutable defensive copies in conversation order."""

        async with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(details={"session_id": session_id})
            return tuple(message.model_copy(deep=True) for message in self._messages[session_id])

    async def clear_session(self, session_id: str) -> None:
        """Clear history atomically without recycling the session identifier."""

        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise SessionNotFoundError(details={"session_id": session_id})
            self._messages[session_id].clear()
            self._sessions[session_id] = record.model_copy(update={"updated_at": utc_now()})
