"""Minimal deterministic tools used to verify the Tool Runtime."""

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.tools.base import ToolDefinition, ToolRiskLevel
from dayu_agent.tools.permissions import ToolPermission
from dayu_agent.tools.registry import ToolRegistry


class HealthInput(BaseModel):
    """Accept no fields for the health tool."""

    model_config = ConfigDict(extra="forbid")


class HealthOutput(BaseModel):
    """Stable health-tool response."""

    status: str


class EchoInput(BaseModel):
    """Validated input for the echo tool."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class EchoOutput(BaseModel):
    """Validated output for the echo tool."""

    text: str


async def _health_handler(_: BaseModel) -> BaseModel:
    """Return a deterministic tool-runtime liveness value."""

    return HealthOutput(status="ok")


async def _echo_handler(payload: BaseModel) -> BaseModel:
    """Return the validated text without side effects."""

    echo_input = cast(EchoInput, payload)
    return EchoOutput(text=echo_input.text)


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register only the two safe Phase-00 proof tools."""

    registry.register(
        ToolDefinition(
            name="system.health",
            description="Return deterministic Tool Runtime liveness.",
            input_model=HealthInput,
            output_model=HealthOutput,
            permission=ToolPermission.READ,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=1.0,
            handler=_health_handler,
        )
    )
    registry.register(
        ToolDefinition(
            name="system.echo",
            description="Echo validated text without side effects.",
            input_model=EchoInput,
            output_model=EchoOutput,
            permission=ToolPermission.READ,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=1.0,
            handler=_echo_handler,
        )
    )


__all__ = ["EchoInput", "EchoOutput", "HealthInput", "HealthOutput", "register_builtin_tools"]
