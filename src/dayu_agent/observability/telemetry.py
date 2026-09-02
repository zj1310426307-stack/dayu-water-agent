"""Optional OpenTelemetry setup and no-op-compatible span helper."""

import importlib
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)
_tracer: Any | None = None
_provider: Any | None = None


def configure_telemetry(*, enabled: bool, endpoint: str | None = None) -> bool:
    """Enable OTEL when optional packages exist, otherwise retain local tracing."""

    global _provider, _tracer
    if not enabled:
        _provider = None
        _tracer = None
        return False
    try:
        trace_api = importlib.import_module("opentelemetry.trace")
        trace_sdk = importlib.import_module("opentelemetry.sdk.trace")
        provider = trace_sdk.TracerProvider()
        if endpoint:
            exporter_module = importlib.import_module(
                "opentelemetry.exporter.otlp.proto.http.trace_exporter"
            )
            processor_module = importlib.import_module("opentelemetry.sdk.trace.export")
            exporter = exporter_module.OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(processor_module.BatchSpanProcessor(exporter))
        trace_api.set_tracer_provider(provider)
        _provider = provider
        _tracer = trace_api.get_tracer("dayu-water-agent")
        return True
    except (ImportError, ModuleNotFoundError):
        logger.warning("OpenTelemetry optional packages are unavailable; using local traces")
        _provider = None
        _tracer = None
        return False


@contextmanager
def telemetry_span(name: str, **attributes: str | int | float | bool | None) -> Iterator[None]:
    """Create a named span when configured and otherwise behave as a no-op."""

    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        yield


def shutdown_telemetry() -> None:
    """Flush optional exporters without making shutdown depend on a collector."""

    global _provider, _tracer
    provider = _provider
    _provider = None
    _tracer = None
    if provider is None:
        return
    try:
        provider.force_flush()
        provider.shutdown()
    except Exception:
        logger.warning("OpenTelemetry shutdown did not complete cleanly")
