"""SQLAlchemy persistence models for sessions, runs, messages, and SSE events."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from dayu_agent.database.base import Base


def _new_id() -> str:
    """Return a portable UUID string for application-generated primary keys."""

    return str(uuid4())


class AgentSessionModel(Base):
    """Persist a conversation boundary and its optimistic version."""

    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    messages: Mapped[list["AgentMessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="AgentMessageModel.sequence",
    )
    runs: Mapped[list["AgentRunModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class AgentRunModel(Base):
    """Persist a durable request execution and its terminal outcome."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_agent_runs_idempotency_key"),
        Index("ix_agent_runs_request_id", "request_id"),
        Index("ix_agent_runs_session_created", "session_id", "created_at"),
        Index(
            "uq_agent_runs_one_active_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    worker_instance_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_json: Mapped[dict[str, Any]] = mapped_column("usage", JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column("result", JSON, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    session: Mapped[AgentSessionModel] = relationship(back_populates="runs")
    messages: Mapped[list["AgentMessageModel"]] = relationship(back_populates="run")
    events: Mapped[list["AgentStreamEventModel"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="AgentStreamEventModel.sequence",
    )


class AgentMessageModel(Base):
    """Persist a committed, ordered message in a session transcript."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_agent_messages_session_sequence"),
        Index("ix_agent_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    committed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    session: Mapped[AgentSessionModel] = relationship(back_populates="messages")
    run: Mapped[AgentRunModel | None] = relationship(back_populates="messages")


class AgentStreamEventModel(Base):
    """Persist one monotonically sequenced SSE event for resumable replay."""

    __tablename__ = "agent_stream_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_stream_events_run_sequence"),
        Index("ix_agent_stream_events_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column("type", String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    run: Mapped[AgentRunModel] = relationship(back_populates="events")
