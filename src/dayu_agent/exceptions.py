"""Domain exception hierarchy used across runtime, API, and tools."""

from collections.abc import Mapping
from typing import Any, ClassVar


class DayuAgentError(Exception):
    """Base safe-to-map application error.

    ``details`` must contain only non-secret, client-safe context. Raw SDK
    exceptions and stack traces are intentionally not included.
    """

    error_code: ClassVar[str] = "DAYU_AGENT_ERROR"
    http_status: ClassVar[int] = 500
    default_message: ClassVar[str] = "Dayu Water Agent could not complete the request."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.details = dict(details) if details is not None else None


class ConfigurationError(DayuAgentError):
    """Raised when required runtime configuration is invalid or absent."""

    error_code = "CONFIGURATION_ERROR"
    http_status = 503
    default_message = "The agent runtime is not configured correctly."


class ProviderError(DayuAgentError):
    """Raised when the selected model provider fails or returns an invalid result."""

    error_code = "PROVIDER_ERROR"
    http_status = 502
    default_message = "The model provider could not complete the request."


class ToolError(DayuAgentError):
    """Base error for controlled Tool Runtime failures."""

    error_code = "TOOL_ERROR"
    http_status = 500
    default_message = "The tool could not complete the request."


class ToolNotFoundError(ToolError):
    """Raised when callers request an unregistered tool."""

    error_code = "TOOL_NOT_FOUND"
    http_status = 404
    default_message = "The requested tool is not registered."


class ToolConflictError(ToolError):
    """Raised when a tool name is registered more than once."""

    error_code = "TOOL_NAME_CONFLICT"
    http_status = 409
    default_message = "A tool with the same name is already registered."


class ToolPermissionError(ToolError):
    """Raised when a tool is denied by permission or risk policy."""

    error_code = "TOOL_PERMISSION_DENIED"
    http_status = 403
    default_message = "The requested tool is not permitted."


class ToolValidationError(ToolError):
    """Raised when tool input or output violates its declared schema."""

    error_code = "TOOL_VALIDATION_ERROR"
    http_status = 422
    default_message = "The tool input or output failed schema validation."


class ToolTimeoutError(ToolError):
    """Raised when a tool exceeds its declared timeout."""

    error_code = "TOOL_TIMEOUT"
    http_status = 504
    default_message = "The tool exceeded its execution timeout."


class SessionError(DayuAgentError):
    """Base error for session storage failures."""

    error_code = "SESSION_ERROR"
    http_status = 500
    default_message = "The session operation failed."


class SessionNotFoundError(SessionError):
    """Raised when a session identifier does not exist."""

    error_code = "SESSION_NOT_FOUND"
    http_status = 404
    default_message = "The requested session does not exist."


class GuardrailError(DayuAgentError):
    """Raised when input, output, or tool policy blocks an operation."""

    error_code = "GUARDRAIL_BLOCKED"
    http_status = 422
    default_message = "A guardrail blocked the request."
