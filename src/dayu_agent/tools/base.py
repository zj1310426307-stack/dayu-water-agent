"""Schema-first tool definition and normalized execution result."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.tools.permissions import ToolPermission

ToolHandler = Callable[[BaseModel], Awaitable[BaseModel | dict[str, Any]]]


class ToolRiskLevel(StrEnum):
    """Risk classification evaluated independently from permission."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    DANGEROUS = "DANGEROUS"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Declare a tool's schemas, policy, timeout, and async handler."""

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    permission: ToolPermission
    risk_level: ToolRiskLevel
    timeout_seconds: float
    handler: ToolHandler


class ToolExecutionResult(BaseModel):
    """Return validated output plus audit-safe timing information."""

    model_config = ConfigDict(frozen=True)

    name: str
    output: dict[str, Any]
    duration_ms: float = Field(ge=0)
