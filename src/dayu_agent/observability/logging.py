"""Dependency-free JSON logging for process and tool observability."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

STANDARD_LOG_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)
SENSITIVE_FIELD_FRAGMENTS = ("api_key", "authorization", "password", "secret", "token")


class JsonFormatter(logging.Formatter):
    """Serialize log records without stack traces or implicit secret fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Build one compact JSON object per record."""

        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in STANDARD_LOG_FIELDS and not key.startswith("_"):
                normalized_key = key.lower()
                payload[key] = (
                    "[REDACTED]"
                    if any(fragment in normalized_key for fragment in SENSITIVE_FIELD_FRAGMENTS)
                    else value
                )
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_logging(level: str) -> None:
    """Configure a single process-wide JSON handler at the application boundary."""

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
