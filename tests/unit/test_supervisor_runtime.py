"""Production runtime behavior tests for idempotency, concurrency, retry, and SSE."""

import asyncio
from collections.abc import Callable

import pytest

from dayu_agent.agents import SupervisorAgent
from dayu_agent.exceptions import (
    IdempotencyConflictError,
    ProviderError,
    RetryBudgetExhaustedError,
    SessionBusyError,
)
from dayu_agent.memory import InMemorySessionStore
from dayu_agent.providers.fake import FakeModelProvider
from dayu_agent.runtime.retry import RetryBudget
from dayu_agent.runtime.state import RunStatus, StreamEventType
from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry


def _supervisor(
    provider: FakeModelProvider,
    *,
    max_attempts: int = 3,
) -> tuple[SupervisorAgent, InMemorySessionStore]:
    """Build a zero-backoff runtime for deterministic lifecycle tests."""

    store = InMemorySessionStore()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return (
        SupervisorAgent(
            provider=provider,
            session_store=store,
            tool_registry=registry,
            retry_budget=RetryBudget(
                max_attempts=max_attempts,
                max_elapsed_seconds=2,
                base_delay=0,
                max_delay=0,
                jitter=False,
            ),
            worker_instance_id="test-worker",
        ),
        store,
    )


async def _yield_until(predicate: Callable[[], bool]) -> None:
    """Give scheduled tasks bounded opportunities to reach a test condition."""

    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("scheduled runtime task did not reach the expected condition")


@pytest.mark.asyncio
async def test_idempotent_concurrent_replay_invokes_provider_once() -> None:
    """The same key and payload share one run and one provider invocation."""

    provider = FakeModelProvider(delay_seconds=0.01)
    supervisor, store = _supervisor(provider)
    session = await supervisor.create_session()
    first, second = await asyncio.gather(
        supervisor.run("same", session_id=session.id, idempotency_key="same-key"),
        supervisor.run("same", session_id=session.id, idempotency_key="same-key"),
    )

    assert first.run_id == second.run_id
    assert provider.call_count == 1
    assert len(await store.list_messages(session.id)) == 2


@pytest.mark.asyncio
async def test_idempotency_key_rejects_different_payload() -> None:
    """Reusing a key with changed request semantics returns a conflict."""

    supervisor, _ = _supervisor(FakeModelProvider())
    session = await supervisor.create_session()
    await supervisor.run("one", session_id=session.id, idempotency_key="fixed-key")

    with pytest.raises(IdempotencyConflictError):
        await supervisor.run("two", session_id=session.id, idempotency_key="fixed-key")


@pytest.mark.asyncio
async def test_same_session_is_fail_fast_while_different_sessions_run_in_parallel() -> None:
    """Session ownership is exclusive without imposing a global execution lock."""

    release = asyncio.Event()
    provider = FakeModelProvider(block_event=release)
    supervisor, _ = _supervisor(provider)
    first_session = await supervisor.create_session()
    second_session = await supervisor.create_session()
    first = asyncio.create_task(supervisor.run("first", session_id=first_session.id))
    second = asyncio.create_task(supervisor.run("second", session_id=second_session.id))
    await _yield_until(lambda: provider.call_count == 2)

    with pytest.raises(SessionBusyError):
        await supervisor.run("busy", session_id=first_session.id)
    release.set()
    await asyncio.gather(first, second)
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_cancellation_stops_provider_and_commits_one_terminal_event() -> None:
    """Cancellation is durable, idempotent, and never commits partial history."""

    release = asyncio.Event()
    provider = FakeModelProvider(block_event=release)
    supervisor, store = _supervisor(provider)
    session = await supervisor.create_session()
    run = await supervisor.start_stream("cancel", session_id=session.id)
    await _yield_until(lambda: provider.call_count == 1)

    first = await supervisor.cancel(run.id)
    second = await supervisor.cancel(run.id)
    events = await store.list_stream_events(run.id)

    assert first.disposition.value == "cancelled"
    assert second.disposition.value == "already_cancelled"
    assert (await store.get_run(run.id)).status is RunStatus.CANCELLED
    assert await store.list_messages(session.id) == ()
    assert [event.type for event in events].count(StreamEventType.RUN_CANCELLED) == 1


@pytest.mark.asyncio
async def test_retry_budget_counts_attempts_and_recovers_transient_failures() -> None:
    """Retryable pre-response failures recover within one application-owned budget."""

    provider = FakeModelProvider(fail_times=2, failure_retryable=True)
    supervisor, store = _supervisor(provider, max_attempts=3)
    result = await supervisor.run("retry")
    run = await store.get_run(result.run_id or "")

    assert provider.call_count == 3
    assert run.attempt_count == 3
    assert run.status is RunStatus.COMPLETED
    metrics = supervisor.metrics.snapshot()
    assert metrics.agent_runs_total == 1
    assert metrics.provider_attempts_total == 3
    assert metrics.provider_retries_total == 2
    assert metrics.active_runs == 0


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_and_nonretryable_failure_are_bounded() -> None:
    """Exhaustion stops at its cap and terminal failures are never retried."""

    retry_provider = FakeModelProvider(fail_times=10, failure_retryable=True)
    retry_supervisor, _ = _supervisor(retry_provider, max_attempts=2)
    with pytest.raises(RetryBudgetExhaustedError):
        await retry_supervisor.run("exhaust")
    assert retry_provider.call_count == 2

    terminal_provider = FakeModelProvider(fail=True)
    terminal_supervisor, _ = _supervisor(terminal_provider, max_attempts=3)
    with pytest.raises(ProviderError):
        await terminal_supervisor.run("terminal")
    assert terminal_provider.call_count == 1


@pytest.mark.asyncio
async def test_stream_resume_replays_only_events_after_cursor() -> None:
    """A reconnect resumes with monotonic IDs and no duplicate durable events."""

    supervisor, _ = _supervisor(
        FakeModelProvider(stream_chunks=("one", "two", "three"))
    )
    run = await supervisor.start_stream("stream")
    first_batch = []
    async for event in supervisor.stream_run(run.id):
        first_batch.append(event)
        if event.type is StreamEventType.RESPONSE_DELTA:
            break
    cursor = first_batch[-1].sequence
    resumed = [event async for event in supervisor.stream_run(run.id, after_sequence=cursor)]

    sequences = [event.sequence for event in first_batch + resumed]
    assert sequences == sorted(set(sequences))
    assert resumed[0].sequence > cursor
    assert resumed[-1].type is StreamEventType.RESPONSE_COMPLETED
    assert supervisor.metrics.snapshot().stream_resume_total == 1


@pytest.mark.asyncio
async def test_stream_never_retries_after_first_persisted_delta() -> None:
    """A failure after response start terminates instead of duplicating text."""

    provider = FakeModelProvider(
        stream_chunks=("partial", "unreachable"),
        stream_fail_after_deltas=1,
        failure_retryable=True,
    )
    supervisor, store = _supervisor(provider, max_attempts=3)
    run = await supervisor.start_stream("stream failure")
    events = [event async for event in supervisor.stream_run(run.id)]

    assert provider.call_count == 1
    assert [event.type for event in events][-2:] == [
        StreamEventType.RESPONSE_DELTA,
        StreamEventType.RUN_FAILED,
    ]
    assert (await store.get_run(run.id)).attempt_count == 1
