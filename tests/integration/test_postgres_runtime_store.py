"""Real PostgreSQL contract tests for the production RuntimeStore adapter."""

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from dayu_agent.agents import SupervisorAgent
from dayu_agent.api.app import create_app
from dayu_agent.config import Settings
from dayu_agent.database.store import SQLAlchemyRuntimeStore
from dayu_agent.exceptions import IdempotencyConflictError, SessionBusyError
from dayu_agent.providers.fake import FakeModelProvider
from dayu_agent.runtime.result import AgentResult, AgentStatus
from dayu_agent.runtime.state import RunReservation, RunStatus, StreamEventType
from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration


def _database_url() -> str:
    """Return the explicit test database URL or skip the live suite."""

    database_url = os.getenv("DAYU_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DAYU_TEST_DATABASE_URL is not configured")
    return database_url


@pytest_asyncio.fixture
async def postgres_store() -> AsyncIterator[SQLAlchemyRuntimeStore]:
    """Yield a store only when an explicit disposable test database is configured."""

    store = SQLAlchemyRuntimeStore(_database_url())
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


async def test_postgres_parallel_same_key_resolves_to_one_owned_run(
    postgres_store: SQLAlchemyRuntimeStore,
) -> None:
    """A lock waiter rechecks idempotency and replays instead of reporting busy."""

    session = await postgres_store.create_session()
    key = f"parallel-idempotency-{uuid4()}"

    async def reserve(request_id: str) -> RunReservation:
        """Race one reservation with identical durable request semantics."""

        return await postgres_store.reserve_run(
            session_id=session.id,
            session_metadata={},
            user_id=None,
            idempotency_key=key,
            request_hash="same-hash",
            request_id=request_id,
            trace_id=str(uuid4()),
            provider="fake",
            model="fake-model",
            worker_instance_id="integration-worker",
            run_metadata={},
        )

    first, second = await asyncio.gather(reserve(str(uuid4())), reserve(str(uuid4())))
    assert first.run.id == second.run.id
    assert sorted([first.owns_execution, second.owns_execution]) == [False, True]
    await postgres_store.fail_run(first.run.id, error_code="TEST_CLEANUP")


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


def _postgres_settings() -> Settings:
    """Build production-like test settings without relying on ambient configuration."""

    return Settings(
        _env_file=None,
        environment="test",
        model_provider="fake",
        model_name="fake-postgres",
        session_store="postgres",
        database_url=_database_url(),
        log_level="CRITICAL",
        retry_jitter=False,
    )


async def test_postgres_api_persists_sessions_and_runs_across_restart() -> None:
    """A new application instance can query the prior committed session and run."""

    first_app = create_app(
        settings=_postgres_settings(),
        provider=FakeModelProvider(model_name="fake-postgres"),
    )
    async with first_app.router.lifespan_context(first_app):
        transport = httpx.ASGITransport(app=first_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/v1/sessions", json={})
            session_id = created.json()["session_id"]
            chat = await client.post(
                "/api/v1/chat",
                json={"message": "persist", "session_id": session_id},
                headers={"Idempotency-Key": f"restart-{uuid4()}"},
            )
            run_id = chat.json()["run_id"]

    second_app = create_app(
        settings=_postgres_settings(),
        provider=FakeModelProvider(model_name="fake-postgres"),
    )
    async with second_app.router.lifespan_context(second_app):
        transport = httpx.ASGITransport(app=second_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            session = await client.get(f"/api/v1/sessions/{session_id}")
            run = await client.get(f"/api/v1/runs/{run_id}")

    assert [item["content"] for item in session.json()["messages"]] == [
        "persist",
        "Fake response (turn 1): persist",
    ]
    assert run.json()["status"] == "completed"


async def test_postgres_api_handles_fifty_parallel_independent_sessions() -> None:
    """Fifty independent requests complete without global serialization or disorder."""

    provider = FakeModelProvider(model_name="fake-postgres", delay_seconds=0.01)
    application = create_app(settings=_postgres_settings(), provider=provider)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await asyncio.gather(
                *(client.post("/api/v1/sessions", json={}) for _ in range(50))
            )
            session_ids = [response.json()["session_id"] for response in created]
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/api/v1/chat",
                        json={"message": f"message-{index}", "session_id": session_id},
                    )
                    for index, session_id in enumerate(session_ids)
                )
            )

    assert all(response.status_code == 200 for response in responses)
    assert provider.call_count == 50
    assert len({response.json()["run_id"] for response in responses}) == 50


async def test_postgres_startup_reconciles_old_active_run_as_interrupted(
    postgres_store: SQLAlchemyRuntimeStore,
) -> None:
    """A new worker never auto-replays an ambiguous prior provider execution."""

    session = await postgres_store.create_session()
    reservation = await postgres_store.reserve_run(
        session_id=session.id,
        session_metadata={},
        user_id=None,
        idempotency_key=f"orphan-{uuid4()}",
        request_hash="orphan-hash",
        request_id=str(uuid4()),
        trace_id=str(uuid4()),
        provider="fake",
        model="fake-model",
        worker_instance_id="old-worker",
        run_metadata={},
    )
    await postgres_store.mark_run_running(reservation.run.id, "old-worker")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    supervisor = SupervisorAgent(
        provider=FakeModelProvider(),
        session_store=postgres_store,
        tool_registry=registry,
        worker_instance_id="new-worker",
    )

    assert await supervisor.initialize() == 1
    reconciled = await postgres_store.get_run(reservation.run.id)
    events = await postgres_store.list_stream_events(reservation.run.id)
    assert reconciled.status is RunStatus.INTERRUPTED
    assert events[-1].type is StreamEventType.RUN_INTERRUPTED
