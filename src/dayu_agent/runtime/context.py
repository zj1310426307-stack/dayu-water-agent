"""Transport-independent context passed through an agent run."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for model defaults."""

    return datetime.now(UTC)


class AgentContext(BaseModel):
    """Identify one agent request without leaking HTTP framework objects."""

    model_config = ConfigDict(frozen=True)

    request_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    session_id: str
    user_id: str | None = None
    agent_name: str = "SupervisorAgent"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
