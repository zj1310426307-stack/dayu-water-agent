"""Explicit permission vocabulary for every registered tool."""

from enum import StrEnum


class ToolPermission(StrEnum):
    """Capabilities ordered by semantic effect, not implied trust."""

    READ = "READ"
    ANALYZE = "ANALYZE"
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    EXECUTE = "EXECUTE"
    DANGEROUS = "DANGEROUS"


DEFAULT_ALLOWED_PERMISSIONS = frozenset({ToolPermission.READ, ToolPermission.ANALYZE})
CONFIRMATION_REQUIRED_PERMISSIONS = frozenset(
    {ToolPermission.MODIFY, ToolPermission.EXECUTE}
)
