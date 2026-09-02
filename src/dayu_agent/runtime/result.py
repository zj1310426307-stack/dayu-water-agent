"""Normalized output returned by every agent execution path."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentStatus(StrEnum):
    """Stable status vocabulary for API and CLI consumers."""

    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    PARTIAL = "partial"


class TokenUsage(BaseModel):
    """Provider-neutral token usage counters."""

    model_config = ConfigDict(frozen=True)

    requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def normalize_total(self) -> "TokenUsage":
        """Fill a missing total while preserving authoritative provider totals."""

        if self.total_tokens == 0 and (self.input_tokens or self.output_tokens):
            object.__setattr__(self, "total_tokens", self.input_tokens + self.output_tokens)
        return self


class ToolCallRecord(BaseModel):
    """Safe audit summary for one Tool Registry execution."""

    model_config = ConfigDict(frozen=True)

    name: str
    status: AgentStatus
    duration_ms: float = Field(ge=0)
    output: dict[str, Any] | None = None
    error_code: str | None = None


class AgentResult(BaseModel):
    """Provider- and transport-neutral agent response."""

    model_config = ConfigDict(frozen=True)

    request_id: str
    session_id: str
    agent: str
    content: str
    status: AgentStatus
    usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_calls: tuple[ToolCallRecord, ...] = ()
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
