"""PostgreSQL implementation of the provider-neutral RuntimeStore contract."""

from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dayu_agent.contracts.session import AgentMessage, MessageRole, SessionRecord, utc_now
from dayu_agent.database.models import (
    AgentMessageModel,
    AgentRunModel,
    AgentSessionModel,
    AgentStreamEventModel,
)
from dayu_agent.exceptions import (
    CancellationUnavailableError,
    DatabaseUnavailableError,
    DayuAgentError,
    IdempotencyConflictError,
    RunNotFoundError,
    SessionBusyError,
    SessionError,
    SessionNotFoundError,
)
from dayu_agent.runtime.result import AgentResult, TokenUsage
from dayu_agent.runtime.state import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_RUN_STATUSES,
    AgentRun,
    CancelDisposition,
    CancelResult,
    RunReservation,
    RunStatus,
    StreamEvent,
    StreamEventType,
    validate_run_transition,
)
from dayu_agent.runtime.store import RuntimeStore

_ACTIVE_VALUES = tuple(status.value for status in ACTIVE_RUN_STATUSES)
_TERMINAL_VALUES = tuple(status.value for status in TERMINAL_RUN_STATUSES)


class SQLAlchemyRuntimeStore(RuntimeStore):
    """Persist runtime state with explicit transactions and row-level locks."""

    def __init__(
        self,
        database_url: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 10.0,
    ) -> None:
        """Create an async engine without creating or migrating any tables."""

        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
        )
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)

    async def initialize(self) -> None:
        """Verify schema connectivity while leaving migrations to Alembic."""

        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM agent_sessions LIMIT 1"))
        except SQLAlchemyError as exc:
            raise self._database_error("initialize") from exc

    async def create_session(
        self,
        *,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionRecord:
        """Create a session in one transaction and reject duplicate identifiers."""

        model = AgentSessionModel(
            id=session_id or str(uuid4()),
            user_id=user_id,
            metadata_json=metadata or {},
        )
        try:
            async with self._sessions.begin() as session:
                session.add(model)
                await session.flush()
                await session.refresh(model)
            return self._session_record(model)
        except IntegrityError as exc:
            raise SessionError(
                "A session with this identifier already exists.",
                details={"session_id": model.id},
            ) from exc
        except SQLAlchemyError as exc:
            raise self._database_error("create_session") from exc

    async def get_session(self, session_id: str) -> SessionRecord:
        """Return one session without exposing ORM state."""

        try:
            async with self._sessions() as session:
                model = await session.get(AgentSessionModel, session_id)
                if model is None:
                    raise SessionNotFoundError(details={"session_id": session_id})
                return self._session_record(model)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("get_session") from exc

    async def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        """Append a compatibility message while excluding active runs."""

        try:
            async with self._sessions.begin() as session:
                session_model = await self._locked_session(session, session_id)
                active = await self._active_run(session, session_id)
                if active is not None:
                    raise SessionBusyError(
                        details={"session_id": session_id, "run_id": active.id}
                    )
                sequence = await self._next_message_sequence(session, session_id)
                model = AgentMessageModel(
                    session_id=session_id,
                    role=role.value,
                    content=content,
                    sequence=sequence,
                    committed=True,
                    metadata_json=metadata or {},
                )
                session.add(model)
                session_model.version += 1
                session_model.updated_at = utc_now()
                await session.flush()
                await session.refresh(model)
            return self._message(model)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("append_message") from exc

    async def list_messages(self, session_id: str) -> tuple[AgentMessage, ...]:
        """Return committed messages in durable sequence order."""

        try:
            async with self._sessions() as session:
                if await session.get(AgentSessionModel, session_id) is None:
                    raise SessionNotFoundError(details={"session_id": session_id})
                statement = (
                    select(AgentMessageModel)
                    .where(
                        AgentMessageModel.session_id == session_id,
                        AgentMessageModel.committed.is_(True),
                    )
                    .order_by(AgentMessageModel.sequence)
                )
                models = (await session.scalars(statement)).all()
                return tuple(self._message(model) for model in models)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("list_messages") from exc

    async def clear_session(self, session_id: str) -> None:
        """Clear transcript history while excluding active session runs."""

        try:
            async with self._sessions.begin() as session:
                session_model = await self._locked_session(session, session_id)
                active = await self._active_run(session, session_id)
                if active is not None:
                    raise SessionBusyError(
                        details={"session_id": session_id, "run_id": active.id}
                    )
                await session.execute(
                    delete(AgentMessageModel).where(AgentMessageModel.session_id == session_id)
                )
                session_model.version += 1
                session_model.updated_at = utc_now()
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("clear_session") from exc

    async def reserve_run(
        self,
        *,
        session_id: str | None,
        session_metadata: dict[str, Any],
        user_id: str | None,
        idempotency_key: str | None,
        request_hash: str,
        request_id: str,
        trace_id: str,
        provider: str,
        model: str,
        worker_instance_id: str,
        run_metadata: dict[str, Any],
    ) -> RunReservation:
        """Reserve execution under idempotency and one-active-run constraints."""

        resolved_session_id = session_id or str(uuid4())
        run_model = AgentRunModel(
            id=str(uuid4()),
            request_id=request_id,
            trace_id=trace_id,
            session_id=resolved_session_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            provider=provider,
            model=model,
            worker_instance_id=worker_instance_id,
            metadata_json=run_metadata,
        )
        try:
            async with self._sessions.begin() as db_session:
                existing = await self._idempotent_run(db_session, idempotency_key, lock=True)
                if existing is not None:
                    return self._idempotent_reservation(existing, request_hash)

                if session_id is None:
                    db_session.add(
                        AgentSessionModel(
                            id=resolved_session_id,
                            user_id=user_id,
                            metadata_json=session_metadata,
                        )
                    )
                    await db_session.flush()
                else:
                    await self._locked_session(db_session, resolved_session_id)

                active = await self._active_run(db_session, resolved_session_id)
                if active is not None:
                    raise SessionBusyError(
                        details={"session_id": resolved_session_id, "run_id": active.id}
                    )
                db_session.add(run_model)
                await db_session.flush()
            return RunReservation(run=self._run(run_model), owns_execution=True)
        except DayuAgentError:
            raise
        except IntegrityError as exc:
            return await self._resolve_reservation_race(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                session_id=resolved_session_id,
                cause=exc,
            )
        except SQLAlchemyError as exc:
            raise self._database_error("reserve_run") from exc

    async def get_run(self, run_id: str) -> AgentRun:
        """Return one durable run without exposing ORM state."""

        try:
            async with self._sessions() as session:
                model = await session.get(AgentRunModel, run_id)
                if model is None:
                    raise RunNotFoundError(details={"run_id": run_id})
                return self._run(model)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("get_run") from exc

    async def mark_run_running(self, run_id: str, worker_instance_id: str) -> AgentRun:
        """Transition pending to running and persist run.started atomically."""

        try:
            async with self._sessions.begin() as session:
                model = await self._locked_run(session, run_id)
                validate_run_transition(RunStatus(model.status), RunStatus.RUNNING)
                model.status = RunStatus.RUNNING.value
                model.started_at = utc_now()
                model.worker_instance_id = worker_instance_id
                await self._append_event(
                    session,
                    model,
                    StreamEventType.RUN_STARTED,
                    {"status": RunStatus.RUNNING.value},
                )
                await session.flush()
            return self._run(model)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("mark_run_running") from exc

    async def increment_run_attempt(self, run_id: str) -> AgentRun:
        """Durably increment the attempt counter before provider invocation."""

        try:
            async with self._sessions.begin() as session:
                model = await self._locked_run(session, run_id)
                if RunStatus(model.status) is not RunStatus.RUNNING:
                    validate_run_transition(RunStatus(model.status), RunStatus.RUNNING)
                model.attempt_count += 1
                await session.flush()
            return self._run(model)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("increment_run_attempt") from exc

    async def append_stream_event(
        self,
        run_id: str,
        event_type: StreamEventType,
        payload: dict[str, Any],
    ) -> StreamEvent:
        """Append one sequenced event while locking its parent run."""

        try:
            async with self._sessions.begin() as session:
                run = await self._locked_run(session, run_id)
                event = await self._append_event(session, run, event_type, payload)
                await session.flush()
                await session.refresh(event)
            return self._event(event)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("append_stream_event") from exc

    async def list_stream_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[StreamEvent, ...]:
        """Replay durable events strictly after the caller cursor."""

        try:
            async with self._sessions() as session:
                if await session.get(AgentRunModel, run_id) is None:
                    raise RunNotFoundError(details={"run_id": run_id})
                statement = (
                    select(AgentStreamEventModel)
                    .where(
                        AgentStreamEventModel.run_id == run_id,
                        AgentStreamEventModel.sequence > after_sequence,
                    )
                    .order_by(AgentStreamEventModel.sequence)
                )
                models = (await session.scalars(statement)).all()
                return tuple(self._event(model) for model in models)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("list_stream_events") from exc

    async def commit_run_success(
        self,
        run_id: str,
        *,
        user_content: str,
        user_metadata: dict[str, Any],
        assistant_content: str,
        assistant_metadata: dict[str, Any],
        result: AgentResult,
    ) -> AgentRun:
        """Commit the complete turn and completion event in one transaction."""

        try:
            async with self._sessions.begin() as session:
                run = await self._locked_run(session, run_id)
                validate_run_transition(RunStatus(run.status), RunStatus.COMPLETED)
                session_model = await self._locked_session(session, run.session_id)
                sequence = await self._next_message_sequence(session, run.session_id)
                session.add_all(
                    [
                        AgentMessageModel(
                            session_id=run.session_id,
                            run_id=run.id,
                            role=MessageRole.USER.value,
                            content=user_content,
                            sequence=sequence,
                            committed=True,
                            metadata_json=user_metadata,
                        ),
                        AgentMessageModel(
                            session_id=run.session_id,
                            run_id=run.id,
                            role=MessageRole.ASSISTANT.value,
                            content=assistant_content,
                            sequence=sequence + 1,
                            committed=True,
                            metadata_json=assistant_metadata,
                        ),
                    ]
                )
                now = utc_now()
                session_model.version += 1
                session_model.updated_at = now
                run.status = RunStatus.COMPLETED.value
                run.completed_at = now
                run.usage_json = result.usage.model_dump(mode="json")
                run.result_json = result.model_dump(mode="json")
                run.error_code = None
                await self._append_event(
                    session,
                    run,
                    StreamEventType.RESPONSE_COMPLETED,
                    {"result": result.model_dump(mode="json")},
                )
                await session.flush()
            return self._run(run)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("commit_run_success") from exc

    async def fail_run(self, run_id: str, *, error_code: str) -> AgentRun:
        """Publish failed once without committing conversation messages."""

        try:
            async with self._sessions.begin() as session:
                run = await self._locked_run(session, run_id)
                if RunStatus(run.status) in TERMINAL_RUN_STATUSES:
                    return self._run(run)
                validate_run_transition(RunStatus(run.status), RunStatus.FAILED)
                run.status = RunStatus.FAILED.value
                run.completed_at = utc_now()
                run.error_code = error_code
                await self._append_event(
                    session,
                    run,
                    StreamEventType.RUN_FAILED,
                    {"status": RunStatus.FAILED.value, "error_code": error_code},
                )
                await session.flush()
            return self._run(run)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("fail_run") from exc

    async def cancel_run(self, run_id: str, *, worker_instance_id: str) -> CancelResult:
        """Cancel a locally owned active run with idempotent terminal results."""

        try:
            async with self._sessions.begin() as session:
                run = await self._locked_run(session, run_id)
                status = RunStatus(run.status)
                if status is RunStatus.CANCELLED:
                    return CancelResult(
                        run=self._run(run),
                        disposition=CancelDisposition.ALREADY_CANCELLED,
                    )
                if status in TERMINAL_RUN_STATUSES:
                    return CancelResult(
                        run=self._run(run),
                        disposition=CancelDisposition.ALREADY_TERMINAL,
                    )
                if run.worker_instance_id != worker_instance_id:
                    raise CancellationUnavailableError(
                        details={
                            "run_id": run.id,
                            "worker_instance_id": run.worker_instance_id,
                        }
                    )
                validate_run_transition(status, RunStatus.CANCELLED)
                run.status = RunStatus.CANCELLED.value
                run.completed_at = utc_now()
                await self._append_event(
                    session,
                    run,
                    StreamEventType.RUN_CANCELLED,
                    {"status": RunStatus.CANCELLED.value},
                )
                await session.flush()
            return CancelResult(run=self._run(run), disposition=CancelDisposition.CANCELLED)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("cancel_run") from exc

    async def interrupt_worker_runs(self, worker_instance_id: str) -> int:
        """Interrupt active runs owned by the shutting-down worker."""

        return await self._interrupt_matching(worker_instance_id, same_worker=True)

    async def reconcile_orphaned_runs(self, worker_instance_id: str) -> int:
        """Interrupt active runs from all prior worker identities."""

        return await self._interrupt_matching(worker_instance_id, same_worker=False)

    async def prune_stream_events(self, *, before: datetime) -> int:
        """Delete expired events only when their parent run is terminal."""

        try:
            async with self._sessions.begin() as session:
                terminal_runs = select(AgentRunModel.id).where(
                    AgentRunModel.status.in_(_TERMINAL_VALUES)
                )
                result = await session.execute(
                    delete(AgentStreamEventModel).where(
                        AgentStreamEventModel.run_id.in_(terminal_runs),
                        AgentStreamEventModel.created_at < before,
                    )
                )
                return int(result.rowcount or 0)  # type: ignore[attr-defined]
        except SQLAlchemyError as exc:
            raise self._database_error("prune_stream_events") from exc

    async def ping(self) -> bool:
        """Report whether a trivial database query succeeds."""

        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except SQLAlchemyError:
            return False

    async def close(self) -> None:
        """Dispose pooled database connections."""

        await self._engine.dispose()

    async def _locked_session(
        self, session: AsyncSession, session_id: str
    ) -> AgentSessionModel:
        """Load and lock a session row within the caller transaction."""

        statement = (
            select(AgentSessionModel)
            .where(AgentSessionModel.id == session_id)
            .with_for_update()
        )
        model = await session.scalar(statement)
        if model is None:
            raise SessionNotFoundError(details={"session_id": session_id})
        return model

    async def _locked_run(self, session: AsyncSession, run_id: str) -> AgentRunModel:
        """Load and lock a run row within the caller transaction."""

        statement = (
            select(AgentRunModel).where(AgentRunModel.id == run_id).with_for_update()
        )
        model = await session.scalar(statement)
        if model is None:
            raise RunNotFoundError(details={"run_id": run_id})
        return model

    async def _active_run(
        self, session: AsyncSession, session_id: str
    ) -> AgentRunModel | None:
        """Return any active run for a session under its already-held lock."""

        statement = select(AgentRunModel).where(
            AgentRunModel.session_id == session_id,
            AgentRunModel.status.in_(_ACTIVE_VALUES),
        )
        return cast(AgentRunModel | None, await session.scalar(statement))

    async def _idempotent_run(
        self,
        session: AsyncSession,
        idempotency_key: str | None,
        *,
        lock: bool,
    ) -> AgentRunModel | None:
        """Resolve an idempotency key, optionally locking an existing row."""

        if idempotency_key is None:
            return None
        statement = select(AgentRunModel).where(
            AgentRunModel.idempotency_key == idempotency_key
        )
        if lock:
            statement = statement.with_for_update()
        return cast(AgentRunModel | None, await session.scalar(statement))

    def _idempotent_reservation(
        self, model: AgentRunModel, request_hash: str
    ) -> RunReservation:
        """Return a replay or reject reuse with a different request hash."""

        if model.request_hash != request_hash:
            raise IdempotencyConflictError(
                details={"idempotency_key": model.idempotency_key, "run_id": model.id}
            )
        return RunReservation(run=self._run(model), owns_execution=False)

    async def _resolve_reservation_race(
        self,
        *,
        idempotency_key: str | None,
        request_hash: str,
        session_id: str,
        cause: IntegrityError,
    ) -> RunReservation:
        """Translate uniqueness races into stable replay or busy outcomes."""

        try:
            async with self._sessions() as session:
                existing = await self._idempotent_run(session, idempotency_key, lock=False)
                if existing is not None:
                    return self._idempotent_reservation(existing, request_hash)
                active = await self._active_run(session, session_id)
                if active is not None:
                    raise SessionBusyError(
                        details={"session_id": session_id, "run_id": active.id}
                    ) from cause
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("resolve_reservation_race") from exc
        raise self._database_error("reserve_run") from cause

    async def _next_message_sequence(self, session: AsyncSession, session_id: str) -> int:
        """Return the next sequence while the parent session row is locked."""

        statement = select(func.coalesce(func.max(AgentMessageModel.sequence), 0)).where(
            AgentMessageModel.session_id == session_id
        )
        return int(await session.scalar(statement) or 0) + 1

    async def _append_event(
        self,
        session: AsyncSession,
        run: AgentRunModel,
        event_type: StreamEventType,
        payload: dict[str, Any],
    ) -> AgentStreamEventModel:
        """Append an event while the parent run row serializes sequence allocation."""

        statement = select(func.coalesce(func.max(AgentStreamEventModel.sequence), 0)).where(
            AgentStreamEventModel.run_id == run.id
        )
        sequence = int(await session.scalar(statement) or 0) + 1
        event = AgentStreamEventModel(
            run_id=run.id,
            sequence=sequence,
            event_type=event_type.value,
            payload_json=payload,
        )
        session.add(event)
        return event

    async def _interrupt_matching(self, worker_instance_id: str, *, same_worker: bool) -> int:
        """Interrupt matching active rows and publish one event for each."""

        try:
            async with self._sessions.begin() as session:
                worker_filter = (
                    AgentRunModel.worker_instance_id == worker_instance_id
                    if same_worker
                    else AgentRunModel.worker_instance_id != worker_instance_id
                )
                statement = (
                    select(AgentRunModel)
                    .where(
                        AgentRunModel.status.in_(_ACTIVE_VALUES),
                        worker_filter,
                    )
                    .with_for_update(skip_locked=True)
                )
                runs = (await session.scalars(statement)).all()
                for run in runs:
                    validate_run_transition(RunStatus(run.status), RunStatus.INTERRUPTED)
                    run.status = RunStatus.INTERRUPTED.value
                    run.completed_at = utc_now()
                    await self._append_event(
                        session,
                        run,
                        StreamEventType.RUN_INTERRUPTED,
                        {"status": RunStatus.INTERRUPTED.value},
                    )
                await session.flush()
                return len(runs)
        except DayuAgentError:
            raise
        except SQLAlchemyError as exc:
            raise self._database_error("interrupt_runs") from exc

    @staticmethod
    def _session_record(model: AgentSessionModel) -> SessionRecord:
        """Convert an ORM session into an immutable runtime contract."""

        return SessionRecord(
            id=model.id,
            user_id=model.user_id,
            status=model.status,
            metadata=model.metadata_json,
            version=model.version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _message(model: AgentMessageModel) -> AgentMessage:
        """Convert an ORM message into an immutable runtime contract."""

        return AgentMessage(
            id=model.id,
            session_id=model.session_id,
            run_id=model.run_id,
            role=MessageRole(model.role),
            content=model.content,
            sequence=model.sequence,
            committed=model.committed,
            metadata=model.metadata_json,
            created_at=model.created_at,
        )

    @staticmethod
    def _run(model: AgentRunModel) -> AgentRun:
        """Convert an ORM run into an immutable runtime contract."""

        return AgentRun(
            id=model.id,
            request_id=model.request_id,
            trace_id=model.trace_id,
            session_id=model.session_id,
            idempotency_key=model.idempotency_key,
            request_hash=model.request_hash,
            status=RunStatus(model.status),
            provider=model.provider,
            model=model.model,
            attempt_count=model.attempt_count,
            worker_instance_id=model.worker_instance_id,
            created_at=model.created_at,
            started_at=model.started_at,
            completed_at=model.completed_at,
            usage=TokenUsage.model_validate(model.usage_json or {}),
            error_code=model.error_code,
            result=model.result_json or None,
            metadata=model.metadata_json or {},
        )

    @staticmethod
    def _event(model: AgentStreamEventModel) -> StreamEvent:
        """Convert an ORM stream event into an immutable runtime contract."""

        return StreamEvent(
            id=model.id,
            run_id=model.run_id,
            sequence=model.sequence,
            type=StreamEventType(model.event_type),
            payload=model.payload_json,
            created_at=model.created_at,
        )

    @staticmethod
    def _database_error(operation: str) -> DatabaseUnavailableError:
        """Build a secret-free database failure for a stable application boundary."""

        return DatabaseUnavailableError(details={"operation": operation})
