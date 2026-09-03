"""State-machine, retry-budget, and in-memory RuntimeStore tests."""

from datetime import UTC, datetime, timedelta

import pytest

from dayu_agent.exceptions import (
    IdempotencyConflictError,
    InvalidRunTransitionError,
    SessionBusyError,
)
from dayu_agent.memory import InMemorySessionStore, MessageRole
from dayu_agent.runtime import AgentResult, AgentStatus, RetryBudget
from dayu_agent.runtime.state import (
    RunReservation,
    RunStatus,
    StreamEventType,
    validate_run_transition,
)


async def reserve(
    store: InMemorySessionStore,
    *,
    session_id: str | None = None,
    key: str | None = "key",
    request_hash: str = "hash",
    worker: str = "worker",
) -> RunReservation:
    """Reserve a deterministic run for focused store tests."""

    return await store.reserve_run(
        session_id=session_id,
        session_metadata={},
        user_id=None,
        idempotency_key=key,
        request_hash=request_hash,
        request_id="request",
        trace_id="trace",
        provider="fake",
        model="fake-test",
        worker_instance_id=worker,
        run_metadata={},
    )


def test_run_state_machine_rejects_terminal_and_reverse_transitions() -> None:
    """Only the frozen pending/running lifecycle may reach a terminal state."""

    validate_run_transition(RunStatus.PENDING, RunStatus.RUNNING)
    validate_run_transition(RunStatus.RUNNING, RunStatus.COMPLETED)
    with pytest.raises(InvalidRunTransitionError):
        validate_run_transition(RunStatus.COMPLETED, RunStatus.RUNNING)
    with pytest.raises(InvalidRunTransitionError):
        validate_run_transition(RunStatus.CANCELLED, RunStatus.COMPLETED)


def test_retry_budget_caps_attempts_elapsed_time_and_jitter() -> None:
    """Attempt and wall-clock limits must both stop retries deterministically."""

    budget = RetryBudget(
        max_attempts=3,
        max_elapsed_seconds=4,
        base_delay=1,
        max_delay=2,
        jitter=False,
    )
    assert budget.delay_after(1) == 1
    assert budget.delay_after(2) == 2
    assert budget.delay_after(3) == 2
    assert budget.permits_retry(failed_attempt=1, elapsed=1, delay=1) is True
    assert budget.permits_retry(failed_attempt=3, elapsed=1, delay=1) is False
    assert budget.permits_retry(failed_attempt=1, elapsed=3.5, delay=1) is False
    jittered = RetryBudget(base_delay=2, max_delay=5, jitter=True)
    assert jittered.delay_after(1, random_value=lambda: 0.0) == 1


@pytest.mark.asyncio
async def test_idempotency_reuses_same_run_and_rejects_different_hash() -> None:
    """An idempotency key is immutable and never creates a second provider owner."""

    store = InMemorySessionStore()
    first = await reserve(store)
    replay = await reserve(store)
    assert first.owns_execution is True
    assert replay.owns_execution is False
    assert replay.run.id == first.run.id
    with pytest.raises(IdempotencyConflictError):
        await reserve(store, request_hash="different")


@pytest.mark.asyncio
async def test_one_active_run_per_session_fails_fast() -> None:
    """Different requests on one active session receive deterministic session_busy."""

    store = InMemorySessionStore()
    session = await store.create_session()
    await reserve(store, session_id=session.id, key="first")
    with pytest.raises(SessionBusyError):
        await reserve(store, session_id=session.id, key="second", request_hash="second")


@pytest.mark.asyncio
async def test_success_commits_complete_turn_and_final_event_atomically() -> None:
    """Only completed runs add paired, ordered committed conversation messages."""

    store = InMemorySessionStore()
    reservation = await reserve(store)
    run = await store.mark_run_running(reservation.run.id, "worker")
    run = await store.increment_run_attempt(run.id)
    result = AgentResult(
        request_id=run.request_id,
        run_id=run.id,
        trace_id=run.trace_id,
        session_id=run.session_id,
        agent="SupervisorAgent",
        content="answer",
        status=AgentStatus.SUCCESS,
    )
    completed = await store.commit_run_success(
        run.id,
        user_content="question",
        user_metadata={},
        assistant_content="answer",
        assistant_metadata={"provider": "fake"},
        result=result,
    )
    messages = await store.list_messages(run.session_id)
    events = await store.list_stream_events(run.id)
    assert completed.status is RunStatus.COMPLETED
    assert completed.attempt_count == 1
    assert [item.role for item in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert [item.sequence for item in messages] == [1, 2]
    assert [item.type for item in events] == [
        StreamEventType.RUN_STARTED,
        StreamEventType.RESPONSE_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_failure_and_cancellation_never_commit_messages() -> None:
    """Failed and cancelled runs remain queryable without polluting conversation context."""

    store = InMemorySessionStore()
    failed = await reserve(store, key="failed")
    await store.mark_run_running(failed.run.id, "worker")
    failed_run = await store.fail_run(failed.run.id, error_code="PROVIDER_UNAVAILABLE")
    assert failed_run.status is RunStatus.FAILED
    assert await store.list_messages(failed.run.session_id) == ()

    cancelled = await reserve(store, session_id=failed.run.session_id, key="cancelled")
    await store.mark_run_running(cancelled.run.id, "worker")
    first = await store.cancel_run(cancelled.run.id, worker_instance_id="worker")
    second = await store.cancel_run(cancelled.run.id, worker_instance_id="worker")
    assert first.disposition == "cancelled"
    assert second.disposition == "already_cancelled"
    assert await store.list_messages(cancelled.run.session_id) == ()


@pytest.mark.asyncio
async def test_reconciliation_and_retention_publish_explainable_state() -> None:
    """A new process interrupts orphaned work and may prune only terminal events."""

    store = InMemorySessionStore()
    reservation = await reserve(store, worker="old")
    await store.mark_run_running(reservation.run.id, "old")
    assert await store.reconcile_orphaned_runs("new") == 1
    run = await store.get_run(reservation.run.id)
    events = await store.list_stream_events(run.id)
    assert run.status is RunStatus.INTERRUPTED
    assert events[-1].type is StreamEventType.RUN_INTERRUPTED
    removed = await store.prune_stream_events(
        before=datetime.now(UTC) + timedelta(seconds=1)
    )
    assert removed == 2
    assert await store.list_stream_events(run.id) == ()
