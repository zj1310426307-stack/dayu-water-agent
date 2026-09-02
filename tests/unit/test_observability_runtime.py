"""Minimal metrics and optional telemetry contract tests."""

from dayu_agent.observability import RuntimeMetrics, configure_telemetry, telemetry_span


def test_runtime_metrics_snapshot_is_consistent() -> None:
    """Required counters and the active gauge update through terminal state."""

    metrics = RuntimeMetrics()
    metrics.run_started()
    metrics.provider_attempt()
    metrics.provider_retry()
    metrics.stream_resume()
    metrics.run_finished(duration_seconds=0.25, failed=True, cancelled=False)
    snapshot = metrics.snapshot()

    assert snapshot.agent_runs_total == 1
    assert snapshot.agent_runs_failed == 1
    assert snapshot.provider_attempts_total == 1
    assert snapshot.provider_retries_total == 1
    assert snapshot.agent_run_duration_count == 1
    assert snapshot.agent_run_duration_sum_seconds == 0.25
    assert snapshot.active_runs == 0
    assert snapshot.stream_resume_total == 1


def test_disabled_telemetry_span_is_a_noop() -> None:
    """A collector and OTEL packages are never required for default startup."""

    assert configure_telemetry(enabled=False) is False
    with telemetry_span("test.noop", request_id="request"):
        observed = True
    assert observed is True
