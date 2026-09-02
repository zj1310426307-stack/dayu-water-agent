"""Fail-closed registry for validation, authorization, and execution."""

import asyncio
import logging
import re
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError

from dayu_agent.exceptions import (
    ToolConflictError,
    ToolError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolTimeoutError,
    ToolValidationError,
)
from dayu_agent.tools.base import ToolDefinition, ToolExecutionResult, ToolRiskLevel
from dayu_agent.tools.permissions import (
    CONFIRMATION_REQUIRED_PERMISSIONS,
    DEFAULT_ALLOWED_PERMISSIONS,
    ToolPermission,
)

logger = logging.getLogger(__name__)
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def _safe_validation_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Remove raw input values from Pydantic errors before they cross boundaries."""

    return [
        {"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
        for item in exc.errors(include_input=False, include_url=False)
    ]


class ToolRegistry:
    """Own tool names and enforce schema, permission, risk, and timeout policy."""

    def __init__(
        self,
        *,
        default_permissions: frozenset[ToolPermission] = DEFAULT_ALLOWED_PERMISSIONS,
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._default_permissions = default_permissions

    def register(self, tool: ToolDefinition) -> None:
        """Register one safe definition and reject conflicts or dangerous tools."""

        if not isinstance(tool.permission, ToolPermission):
            raise ToolPermissionError(
                "Unknown tool permission is denied.",
                details={"tool_name": tool.name},
            )
        if not isinstance(tool.risk_level, ToolRiskLevel):
            raise ToolPermissionError(
                "Unknown tool risk level is denied.",
                details={"tool_name": tool.name},
            )
        if not TOOL_NAME_PATTERN.fullmatch(tool.name):
            raise ToolValidationError(
                "Tool names must use a dotted lowercase namespace.",
                details={"tool_name": tool.name},
            )
        if tool.timeout_seconds <= 0:
            raise ToolValidationError(
                "Tool timeout must be greater than zero.",
                details={"tool_name": tool.name},
            )
        if not issubclass(tool.input_model, BaseModel) or not issubclass(
            tool.output_model, BaseModel
        ):
            raise ToolValidationError(
                "Tool input and output schemas must be Pydantic models.",
                details={"tool_name": tool.name},
            )
        if (
            tool.permission is ToolPermission.DANGEROUS
            or tool.risk_level is ToolRiskLevel.DANGEROUS
        ):
            raise ToolPermissionError(
                "Dangerous tools are prohibited in AGENT-PHASE-00.",
                details={"tool_name": tool.name},
            )
        if tool.name in self._tools:
            raise ToolConflictError(details={"tool_name": tool.name})
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> ToolDefinition:
        """Remove and return a tool, rejecting unknown names."""

        try:
            return self._tools.pop(name)
        except KeyError as exc:
            raise ToolNotFoundError(details={"tool_name": name}) from exc

    def get(self, name: str) -> ToolDefinition:
        """Return a registered tool without exposing registry mutation."""

        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(details={"tool_name": name}) from exc

    def list(self) -> tuple[ToolDefinition, ...]:
        """Return definitions in stable name order."""

        return tuple(self._tools[name] for name in sorted(self._tools))

    async def execute(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        granted_permissions: frozenset[ToolPermission] | None = None,
        confirmed: bool = False,
    ) -> ToolExecutionResult:
        """Validate, authorize, time-bound, execute, and validate one tool call."""

        tool = self.get(name)
        allowed = (
            self._default_permissions if granted_permissions is None else granted_permissions
        )
        if tool.permission not in allowed:
            raise ToolPermissionError(details={"tool_name": name, "permission": tool.permission})
        if tool.permission in CONFIRMATION_REQUIRED_PERMISSIONS and not confirmed:
            raise ToolPermissionError(
                "This tool requires explicit human confirmation.",
                details={"tool_name": name, "permission": tool.permission},
            )

        try:
            validated_input = tool.input_model.model_validate(payload)
        except ValidationError as exc:
            raise ToolValidationError(
                details={"tool_name": name, "errors": _safe_validation_errors(exc)}
            ) from exc

        started = perf_counter()
        try:
            async with asyncio.timeout(tool.timeout_seconds):
                raw_output = await tool.handler(validated_input)
        except TimeoutError as exc:
            raise ToolTimeoutError(details={"tool_name": name}) from exc
        except ToolError:
            raise
        except Exception as exc:
            logger.error(
                "Tool handler failed",
                extra={"tool_name": name, "error": type(exc).__name__},
            )
            raise ToolError(details={"tool_name": name}) from exc

        try:
            validated_output = tool.output_model.model_validate(raw_output)
        except ValidationError as exc:
            raise ToolValidationError(
                "The tool output failed schema validation.",
                details={"tool_name": name, "errors": _safe_validation_errors(exc)},
            ) from exc

        duration_ms = (perf_counter() - started) * 1000
        logger.info(
            "Tool execution completed",
            extra={
                "tool_name": name,
                "tool_duration": round(duration_ms, 3),
                "permission": tool.permission,
            },
        )
        return ToolExecutionResult(
            name=name,
            output=validated_output.model_dump(mode="json"),
            duration_ms=duration_ms,
        )
