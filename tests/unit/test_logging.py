"""Structured logging safety tests."""

import json
import logging

from dayu_agent.observability.logging import JsonFormatter


def test_json_formatter_redacts_sensitive_extra_fields() -> None:
    """Known credential field names must never be serialized verbatim."""

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe",
        args=(),
        exc_info=None,
    )
    record.api_key = "secret-value"
    record.request_id = "request"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["api_key"] == "[REDACTED]"
    assert payload["request_id"] == "request"
