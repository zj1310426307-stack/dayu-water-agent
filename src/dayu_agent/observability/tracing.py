"""Local trace abstraction that does not require a SaaS backend."""

import logging
from time import perf_counter
from typing import Any

from dayu_agent.runtime.context import AgentContext

logger = logging.getLogger(__name__)


class TraceContext:
    """Record request correlation, duration, provider, model, and safe attributes."""

    def __init__(self, context: AgentContext, *, provider: str, model: str) -> None:
        self.context = context
        self.provider = provider
        self.model = model
        self.attributes: dict[str, Any] = {}
        self.duration_ms: float | None = None
        self._started: float | None = None

    def __enter__(self) -> "TraceContext":
        """Start timing and emit a structured trace-start event."""

        self._started = perf_counter()
        logger.info("Agent trace started", extra=self._log_fields())
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Finish timing and emit safe completion or failure metadata."""

        del traceback
        if self._started is not None:
            self.duration_ms = (perf_counter() - self._started) * 1000
        fields = self._log_fields()
        fields["duration"] = round(self.duration_ms or 0, 3)
        if exc_type is not None:
            fields["error"] = exc_type.__name__
            logger.error("Agent trace failed", extra=fields)
            return
        logger.info("Agent trace completed", extra=fields)

    def annotate(self, **attributes: Any) -> None:
        """Attach additional non-secret values to subsequent trace logs."""

        self.attributes.update(attributes)

    def _log_fields(self) -> dict[str, Any]:
        """Build the required correlation fields for structured logs."""

        return {
            "request_id": self.context.request_id,
            "run_id": self.context.run_id,
            "trace_id": self.context.trace_id,
            "session_id": self.context.session_id,
            "agent_name": self.context.agent_name,
            "provider": self.provider,
            "model": self.model,
            **self.attributes,
        }
