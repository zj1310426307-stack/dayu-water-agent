"""Schema, permission, timeout, and error tests for the Tool Registry."""

import asyncio
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.exceptions import (
    ToolConflictError,
    ToolError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolTimeoutError,
    ToolValidationError,
)
from dayu_agent.tools import ToolDefinition, ToolPermission, ToolRegistry, ToolRiskLevel
from dayu_agent.tools.builtin import register_builtin_tools


class NumberInput(BaseModel):
    """Positive-number test input."""

    model_config = ConfigDict(extra="forbid")
    value: int = Field(gt=0)


class NumberOutput(BaseModel):
    """Integer test output."""

    doubled: int


async def double_handler(payload: BaseModel) -> BaseModel:
    """Return twice the validated integer."""

    number = cast(NumberInput, payload)
    return NumberOutput(doubled=number.value * 2)


def make_tool(
    *,
    name: str = "test.double",
    permission: ToolPermission = ToolPermission.READ,
    risk: ToolRiskLevel = ToolRiskLevel.LOW,
    timeout: float = 1.0,
) -> ToolDefinition:
    """Create a reusable valid tool definition."""

    return ToolDefinition(
        name=name,
        description="Double a positive number.",
        input_model=NumberInput,
        output_model=NumberOutput,
        permission=permission,
        risk_level=risk,
        timeout_seconds=timeout,
        handler=double_handler,
    )


@pytest.mark.asyncio
async def test_register_get_list_execute_and_unregister() -> None:
    """The full registry lifecycle must preserve schemas and stable ordering."""

    registry = ToolRegistry()
    registry.register(make_tool(name="test.zeta"))
    registry.register(make_tool(name="test.alpha"))
    assert [tool.name for tool in registry.list()] == ["test.alpha", "test.zeta"]
    assert registry.get("test.alpha").description.startswith("Double")
    result = await registry.execute("test.alpha", {"value": 3})
    assert result.output == {"doubled": 6}
    assert registry.unregister("test.alpha").name == "test.alpha"
    with pytest.raises(ToolNotFoundError):
        registry.get("test.alpha")


def test_duplicate_unknown_and_invalid_names_are_rejected() -> None:
    """Name collisions and malformed names must fail instead of overwriting state."""

    registry = ToolRegistry()
    registry.register(make_tool())
    with pytest.raises(ToolConflictError):
        registry.register(make_tool())
    with pytest.raises(ToolNotFoundError):
        registry.unregister("test.missing")
    with pytest.raises(ToolValidationError):
        registry.register(make_tool(name="INVALID"))
    with pytest.raises(ToolValidationError):
        registry.register(make_tool(name="test.zero", timeout=0))


@pytest.mark.asyncio
async def test_input_and_output_schema_validation() -> None:
    """Both sides of the tool boundary must be validated."""

    registry = ToolRegistry()
    registry.register(make_tool())
    with pytest.raises(ToolValidationError) as input_error:
        await registry.execute("test.double", {"value": 0, "extra": "denied"})
    assert "denied" not in str(input_error.value.details)

    async def invalid_output(_: BaseModel) -> dict[str, str]:
        return {"doubled": "not-an-integer"}

    registry.register(
        ToolDefinition(
            name="test.invalid_output",
            description="Return an invalid output for validation testing.",
            input_model=NumberInput,
            output_model=NumberOutput,
            permission=ToolPermission.READ,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=1,
            handler=invalid_output,
        )
    )
    with pytest.raises(ToolValidationError, match="output"):
        await registry.execute("test.invalid_output", {"value": 1})


@pytest.mark.asyncio
async def test_permissions_confirmation_and_dangerous_policy() -> None:
    """Side-effect permissions require grants/confirmation and dangerous tools never register."""

    registry = ToolRegistry()
    registry.register(make_tool(name="test.modify", permission=ToolPermission.MODIFY))
    with pytest.raises(ToolPermissionError):
        await registry.execute("test.modify", {"value": 1})
    with pytest.raises(ToolPermissionError, match="confirmation"):
        await registry.execute(
            "test.modify",
            {"value": 1},
            granted_permissions=frozenset({ToolPermission.MODIFY}),
        )
    result = await registry.execute(
        "test.modify",
        {"value": 2},
        granted_permissions=frozenset({ToolPermission.MODIFY}),
        confirmed=True,
    )
    assert result.output == {"doubled": 4}
    read_registry = ToolRegistry()
    read_registry.register(make_tool())
    with pytest.raises(ToolPermissionError):
        await read_registry.execute(
            "test.double",
            {"value": 1},
            granted_permissions=frozenset(),
        )
    with pytest.raises(ToolPermissionError, match="prohibited"):
        registry.register(
            make_tool(
                name="test.dangerous",
                permission=ToolPermission.DANGEROUS,
                risk=ToolRiskLevel.DANGEROUS,
            )
        )


def test_unknown_permission_and_risk_fail_closed() -> None:
    """Runtime-invalid enum values must be rejected even if type checks were bypassed."""

    registry = ToolRegistry()
    unknown_permission = make_tool(name="test.unknown_permission")
    object.__setattr__(unknown_permission, "permission", "UNKNOWN")
    with pytest.raises(ToolPermissionError, match="Unknown tool permission"):
        registry.register(unknown_permission)

    unknown_risk = make_tool(name="test.unknown_risk")
    object.__setattr__(unknown_risk, "risk_level", "UNKNOWN")
    with pytest.raises(ToolPermissionError, match="Unknown tool risk"):
        registry.register(unknown_risk)


@pytest.mark.asyncio
async def test_timeout_and_handler_errors_are_normalized() -> None:
    """Timeouts and arbitrary handler exceptions must remain controlled failures."""

    async def slow_handler(_: BaseModel) -> BaseModel:
        await asyncio.sleep(0.05)
        return NumberOutput(doubled=2)

    async def broken_handler(_: BaseModel) -> BaseModel:
        raise RuntimeError("sensitive internal detail")

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="test.slow",
            description="Timeout test.",
            input_model=NumberInput,
            output_model=NumberOutput,
            permission=ToolPermission.READ,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=0.001,
            handler=slow_handler,
        )
    )
    registry.register(
        ToolDefinition(
            name="test.broken",
            description="Error test.",
            input_model=NumberInput,
            output_model=NumberOutput,
            permission=ToolPermission.READ,
            risk_level=ToolRiskLevel.LOW,
            timeout_seconds=1,
            handler=broken_handler,
        )
    )
    with pytest.raises(ToolTimeoutError):
        await registry.execute("test.slow", {"value": 1})
    with pytest.raises(ToolError) as error:
        await registry.execute("test.broken", {"value": 1})
    assert "sensitive internal detail" not in str(error.value)


@pytest.mark.asyncio
async def test_builtin_tools_are_minimal_and_deterministic() -> None:
    """Phase-00 must expose only safe health and echo proof tools."""

    registry = ToolRegistry()
    register_builtin_tools(registry)
    assert [tool.name for tool in registry.list()] == ["system.echo", "system.health"]
    assert (await registry.execute("system.health", {})).output == {"status": "ok"}
    assert (await registry.execute("system.echo", {"text": "hello"})).output == {
        "text": "hello"
    }
