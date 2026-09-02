"""Dependency-free process-local metrics contract for runtime instrumentation."""

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    """Immutable minimal runtime counters and duration aggregation."""

    agent_runs_total: int
    agent_runs_failed: int
    agent_runs_cancelled: int
    provider_attempts_total: int
    provider_retries_total: int
    agent_run_duration_count: int
    agent_run_duration_sum_seconds: float
    active_runs: int
    stream_resume_total: int


class RuntimeMetrics:
    """Record bounded in-process metrics without requiring a monitoring stack."""

    def __init__(self) -> None:
        """Initialize all counters and protect updates for mixed execution contexts."""

        self._lock = Lock()
        self._agent_runs_total = 0
        self._agent_runs_failed = 0
        self._agent_runs_cancelled = 0
        self._provider_attempts_total = 0
        self._provider_retries_total = 0
        self._agent_run_duration_count = 0
        self._agent_run_duration_sum_seconds = 0.0
        self._active_runs = 0
        self._stream_resume_total = 0

    def run_started(self) -> None:
        """Count one newly owned run and increment the active gauge."""

        with self._lock:
            self._agent_runs_total += 1
            self._active_runs += 1

    def run_finished(self, *, duration_seconds: float, failed: bool, cancelled: bool) -> None:
        """Observe one terminal owned run exactly once."""

        with self._lock:
            self._active_runs = max(0, self._active_runs - 1)
            self._agent_run_duration_count += 1
            self._agent_run_duration_sum_seconds += max(0.0, duration_seconds)
            if failed:
                self._agent_runs_failed += 1
            if cancelled:
                self._agent_runs_cancelled += 1

    def provider_attempt(self) -> None:
        """Count one provider invocation before it begins."""

        with self._lock:
            self._provider_attempts_total += 1

    def provider_retry(self) -> None:
        """Count one retry scheduled after a retryable failure."""

        with self._lock:
            self._provider_retries_total += 1

    def stream_resume(self) -> None:
        """Count a stream consumer that supplied a positive resume cursor."""

        with self._lock:
            self._stream_resume_total += 1

    def snapshot(self) -> MetricsSnapshot:
        """Return a consistent immutable copy for diagnostics or exporters."""

        with self._lock:
            return MetricsSnapshot(
                agent_runs_total=self._agent_runs_total,
                agent_runs_failed=self._agent_runs_failed,
                agent_runs_cancelled=self._agent_runs_cancelled,
                provider_attempts_total=self._provider_attempts_total,
                provider_retries_total=self._provider_retries_total,
                agent_run_duration_count=self._agent_run_duration_count,
                agent_run_duration_sum_seconds=self._agent_run_duration_sum_seconds,
                active_runs=self._active_runs,
                stream_resume_total=self._stream_resume_total,
            )
