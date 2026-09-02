"""Real PostgreSQL contract tests for the production RuntimeStore adapter."""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio

from dayu_agent.database.store import SQLAlchemyRuntimeStore
from dayu_agent.exceptions import IdempotencyConflictError, SessionBusyError
from dayu_agent.runtime.result import AgentResult, AgentStatus
from dayu_agent.runtime.state import RunReservation, RunStatus, StreamEventType

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def postgres_store() -> AsyncIterator[SQLAlchemyRuntimeStore]:
    """Yield a store only when an explicit disposable test database is configured."""

    database_url = os.getenv("DAYU_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DAYU_TEST_DATABASE_URL is not configured")
    store = SQLAlchemyRuntimeStore(database_url)
    await store.initialize()
    try:
        yield store
    finally:
        await store.close()


async def _reserve(
    store: SQLAlchemyRuntimeStore,
    *,
    session_id: str,
    suffix: str,
) -> RunReservation:
    """Reserve a uniquely identified fake-provider run for test setup."""

    return await store.reserve_run(
        session_id=session_id,
        session_metadata={},
        user_id=None,
        idempotency_key=f"postgres-{suffix}-{uuid4()}",
        request_hash=f"hash-{suffix}",
        request_id=str(uuid4()),
        trace_id=str(uuid4()),
        provider="fake",
        model="fake-model",
        worker_instance_id="integration-worker",
        run_metadata={},
    )


async def test_postgres_success_commit_and_idempotent_replay(
    postgres_store: SQLAlchemyRuntimeStore,
) -> None:
    """A successful run atomically stores its pair, result, and terminal event."""

    session = await postgres_store.create_session(metadata={"test": True})
    key = f"postgres-replay-{uuid4()}"
    request_id = str(uuid4())
    trace_id = str(uuid4())
    reservation = await postgres_store.reserve_run(
        session_id=session.id,
        session_metadata={},
        user_id=None,
        idempotency_key=key,
        request_hash="stable-hash",
        request_id=request_id,
        trace_id=trace_id,
        provider="fake",
        model="fake-model",
        worker_instance_id="integration-worker",
        run_metadata={},
    )
    await postgres_store.mark_run_running(reservation.run.id, "integration-worker")
    await postgres_store.increment_run_attempt(reservation.run.id)
    result = AgentResult(
        request_id=request_id,
        run_id=reservation.run.id,
        trace_id=trace_id,
        session_id=session.id,
        agent="supervisor",
        content="done",
        status=AgentStatus.SUCCESS,
    )
    completed = await postgres_store.commit_run_success(
        reservation.run.id,
        user_content="question",
        user_metadata={},
        assistant_content="done",
        assistant_metadata={},
        result=result,
    )

    assert completed.status is RunStatus.COMPLETED
    assert completed.attempt_count == 1
    assert [message.content for message in await postgres_store.list_messages(session.id)] == [
        "question",
        "done",
    ]
    events = await postgres_store.list_stream_events(reservation.run.id)
    assert [event.type for event in events] == [
        StreamEventType.RUN_STARTED,
        StreamEventType.RESPONSE_COMPLETED,
    ]

    replay = await postgres_store.reserve_run(
        session_id=session.id,
        session_metadata={},
        user_id=None,
        idempotency_key=key,
        request_hash="stable-hash",
        request_id=str(uuid4()),
        trace_id=str(uuid4()),
        provider="fake",
        model="fake-model",
        worker_instance_id="other-worker",
        run_metadata={},
    )
    assert replay.run.id == reservation.run.id
    assert replay.owns_execution is False

    with pytest.raises(IdempotencyConflictError):
        await postgres_store.reserve_run(
            session_id=session.id,
            session_metadata={},
            user_id=None,
            idempotency_key=key,
            request_hash="different-hash",
            request_id=str(uuid4()),
            trace_id=str(uuid4()),
            provider="fake",
            model="fake-model",
            worker_instance_id="other-worker",
            run_metadata={},
        )


async def test_postgres_serializes_parallel_same_session_reservations(
    postgres_store: SQLAlchemyRuntimeStore,
) -> None:
    """Concurrent requests for one session produce one owner and one busy error."""

    session = await postgres_store.create_session()
    outcomes = await asyncio.gather(
        _reserve(postgres_store, session_id=session.id, suffix="a"),
        _reserve(postgres_store, session_id=session.id, suffix="b"),
        return_exceptions=True,
    )

    reservations = [item for item in outcomes if isinstance(item, RunReservation)]
    errors = [item for item in outcomes if isinstance(item, SessionBusyError)]
    assert len(reservations) == 1
    assert len(errors) == 1
    await postgres_store.fail_run(reservations[0].run.id, error_code="TEST_CLEANUP")


async def test_postgres_failure_does_not_pollute_committed_history(
    postgres_store: SQLAlchemyRuntimeStore,
) -> None:
    """Failed execution remains queryable but contributes no messages."""

    session = await postgres_store.create_session()
    reservation = await _reserve(postgres_store, session_id=session.id, suffix="failure")
    await postgres_store.mark_run_running(reservation.run.id, "integration-worker")
    failed = await postgres_store.fail_run(
        reservation.run.id, error_code="PROVIDER_UNAVAILABLE"
    )

    assert failed.status is RunStatus.FAILED
    assert await postgres_store.list_messages(session.id) == ()
