"""FastAPI transport for the persistent production-grade Supervisor runtime."""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from dayu_agent import __version__
from dayu_agent.agents import SupervisorAgent
from dayu_agent.api.schemas import (
    CancelResponse,
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    ReadyResponse,
    RunResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionResponse,
)
from dayu_agent.config import Settings, get_settings
from dayu_agent.database import SQLAlchemyRuntimeStore
from dayu_agent.exceptions import DayuAgentError
from dayu_agent.memory import InMemorySessionStore
from dayu_agent.observability import (
    configure_logging,
    configure_telemetry,
    shutdown_telemetry,
    telemetry_span,
)
from dayu_agent.providers import ModelProvider, build_provider
from dayu_agent.runtime.retry import RetryBudget
from dayu_agent.runtime.state import StreamEvent
from dayu_agent.runtime.store import RuntimeStore
from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Own dependencies selected explicitly at the application boundary."""

    settings: Settings
    provider: ModelProvider
    session_store: RuntimeStore
    tool_registry: ToolRegistry
    supervisor: SupervisorAgent


def _build_store(settings: Settings) -> RuntimeStore:
    """Select one configured store without any silent fallback."""

    if settings.session_store == "memory":
        return InMemorySessionStore()
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is required for the PostgreSQL store")
    return SQLAlchemyRuntimeStore(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
    )


def build_container(
    settings: Settings,
    *,
    provider: ModelProvider | None = None,
    session_store: RuntimeStore | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ApplicationContainer:
    """Construct an explicit dependency graph suitable for tests and production."""

    selected_provider = provider or build_provider(settings)
    selected_store = session_store or _build_store(settings)
    selected_registry = tool_registry or ToolRegistry()
    if not selected_registry.list():
        register_builtin_tools(selected_registry)
    supervisor = SupervisorAgent(
        provider=selected_provider,
        session_store=selected_store,
        tool_registry=selected_registry,
        retry_budget=RetryBudget(
            max_attempts=settings.retry_max_attempts,
            max_elapsed_seconds=settings.retry_max_elapsed_seconds,
            base_delay=settings.retry_base_delay_seconds,
            max_delay=settings.retry_max_delay_seconds,
            jitter=settings.retry_jitter,
        ),
    )
    return ApplicationContainer(
        settings=settings,
        provider=selected_provider,
        session_store=selected_store,
        tool_registry=selected_registry,
        supervisor=supervisor,
    )


def _request_id(request: Request) -> str:
    """Return middleware correlation identity or create a defensive fallback."""

    return str(getattr(request.state, "request_id", str(uuid4())))


def _safe_validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Strip raw inputs and documentation URLs from validation diagnostics."""

    return [
        {"location": list(item["loc"]), "type": item["type"], "message": item["msg"]}
        for item in exc.errors()
    ]


def _error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    request_id: str,
    details: Any | None = None,
) -> JSONResponse:
    """Build the only error shape returned by non-streaming HTTP boundaries."""

    payload = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=request_id,
        details=details,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _sse_frame(event: StreamEvent) -> str:
    """Serialize one durable event with a resumable SSE identifier."""

    data = json.dumps(event.payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.sequence}\nevent: {event.type.value}\ndata: {data}\n\n"


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    session_store: RuntimeStore | None = None,
    tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    """Create an injectable app with startup reconciliation and graceful shutdown."""

    selected_settings = settings or get_settings()
    configure_logging(selected_settings.log_level)
    configure_telemetry(
        enabled=selected_settings.otel_enabled,
        endpoint=selected_settings.otel_exporter_otlp_endpoint,
    )
    container = build_container(
        selected_settings,
        provider=provider,
        session_store=session_store,
        tool_registry=tool_registry,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Initialize dependencies, reconcile crashes, and drain owned work."""

        interrupted = await container.supervisor.initialize()
        if interrupted:
            logger.warning(
                "Reconciled orphaned agent runs",
                extra={"interrupted_runs": interrupted},
            )
        try:
            yield
        finally:
            await container.supervisor.shutdown()
            await container.session_store.close()
            shutdown_telemetry()

    application = FastAPI(
        title="Dayu Water Agent API",
        version=__version__,
        description="Independent AGENT-PHASE-01 production runtime.",
        lifespan=lifespan,
    )
    application.state.container = container

    @application.middleware("http")
    async def add_request_id_and_limit_body(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach correlation identity and reject declared oversized bodies."""

        request.state.request_id = str(uuid4())
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                oversized = int(content_length) > selected_settings.max_request_bytes
            except ValueError:
                oversized = False
            if oversized:
                response: Response = _error_response(
                    status_code=413,
                    error_code="REQUEST_TOO_LARGE",
                    message="The request body exceeds the configured size limit.",
                    request_id=request.state.request_id,
                )
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        with telemetry_span(
            "http.request",
            request_id=request.state.request_id,
            http_method=request.method,
            http_route=request.url.path,
        ):
            response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.exception_handler(DayuAgentError)
    async def handle_dayu_error(request: Request, exc: DayuAgentError) -> JSONResponse:
        """Map domain errors without exposing causes, paths, or stack traces."""

        return _error_response(
            status_code=exc.http_status,
            error_code=exc.error_code,
            message=exc.message,
            request_id=_request_id(request),
            details=exc.details,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        """Normalize FastAPI and Pydantic request validation failures."""

        return _error_response(
            status_code=422,
            error_code="REQUEST_VALIDATION_ERROR",
            message="The request body failed validation.",
            request_id=_request_id(request),
            details={"errors": _safe_validation_details(exc)},
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Fail closed while logging only safe correlation and exception type."""

        logger.error(
            "Unhandled API error",
            extra={"request_id": _request_id(request), "error": type(exc).__name__},
        )
        return _error_response(
            status_code=500,
            error_code="INTERNAL_ERROR",
            message="An internal error occurred.",
            request_id=_request_id(request),
        )

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Report process liveness without probing dependencies."""

        return HealthResponse(status="alive", service="dayu-water-agent", version=__version__)

    @application.get(
        "/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ErrorResponse}},
    )
    async def ready(request: Request) -> ReadyResponse | JSONResponse:
        """Probe runtime admission, selected storage, and provider readiness."""

        store_ready = await container.session_store.ping()
        provider_health = await container.provider.health()
        components = {
            "runtime": container.supervisor.accepting,
            "store": store_ready,
            "provider": provider_health.ready,
        }
        if not all(components.values()):
            return _error_response(
                status_code=503,
                error_code="NOT_READY",
                message="The agent runtime is not ready.",
                request_id=_request_id(request),
                details={"components": components},
            )
        return ReadyResponse(
            status="ready",
            provider=provider_health.provider,
            model=provider_health.model,
            detail=provider_health.detail,
            components=components,
        )

    @application.post(
        "/api/v1/sessions",
        response_model=SessionResponse,
        status_code=201,
    )
    async def create_session(payload: SessionCreateRequest) -> SessionResponse:
        """Create a persistent or explicitly process-local session."""

        session = await container.supervisor.create_session(
            payload.metadata, user_id=payload.user_id
        )
        return SessionResponse(
            session_id=session.id,
            user_id=session.user_id,
            status=session.status,
            version=session.version,
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @application.get(
        "/api/v1/sessions/{session_id}",
        response_model=SessionDetailResponse,
    )
    async def get_session(session_id: str) -> SessionDetailResponse:
        """Return session metadata and committed ordered history."""

        session = await container.session_store.get_session(session_id)
        messages = await container.session_store.list_messages(session_id)
        return SessionDetailResponse(
            session_id=session.id,
            user_id=session.user_id,
            status=session.status,
            version=session.version,
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages=messages,
        )

    @application.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(
        payload: ChatRequest,
        request: Request,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=255)
        ] = None,
    ) -> ChatResponse:
        """Run or replay one non-streaming Supervisor turn."""

        result = await container.supervisor.run(
            payload.message,
            session_id=payload.session_id,
            user_id=payload.user_id,
            metadata=payload.metadata,
            request_id=_request_id(request),
            idempotency_key=idempotency_key,
        )
        return ChatResponse.model_validate(result.model_dump())

    @application.post("/api/v1/chat/stream")
    async def chat_stream(
        payload: ChatRequest,
        request: Request,
        idempotency_key: Annotated[
            str | None, Header(alias="Idempotency-Key", max_length=255)
        ] = None,
    ) -> StreamingResponse:
        """Start or replay a run and tail its persisted SSE protocol."""

        request_id = _request_id(request)
        run = await container.supervisor.start_stream(
            payload.message,
            session_id=payload.session_id,
            user_id=payload.user_id,
            metadata=payload.metadata,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

        async def event_source() -> AsyncIterator[str]:
            """Yield only events that have already been committed to the store."""

            async for event in container.supervisor.stream_run(run.id):
                yield _sse_frame(event)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Request-ID": request_id,
                "X-Run-ID": run.id,
            },
        )

    @application.get("/api/v1/runs/{run_id}", response_model=RunResponse)
    async def get_run(run_id: str) -> RunResponse:
        """Return durable status, attempts, usage, and safe failure data."""

        run = await container.supervisor.get_run(run_id)
        return RunResponse.model_validate(run.model_dump())

    @application.post("/api/v1/runs/{run_id}/cancel", response_model=CancelResponse)
    async def cancel_run(run_id: str) -> CancelResponse:
        """Request idempotent cancellation from the process owning provider work."""

        outcome = await container.supervisor.cancel(run_id)
        return CancelResponse(
            disposition=outcome.disposition,
            run=RunResponse.model_validate(outcome.run.model_dump()),
        )

    @application.get("/api/v1/runs/{run_id}/stream")
    async def resume_stream(
        run_id: str,
        last_event_id: Annotated[
            int | None, Header(alias="Last-Event-ID", ge=0)
        ] = None,
    ) -> StreamingResponse:
        """Replay durable events after Last-Event-ID and tail an active run."""

        await container.supervisor.get_run(run_id)

        async def event_source() -> AsyncIterator[str]:
            """Resume strictly after the validated sequence cursor."""

            async for event in container.supervisor.stream_run(
                run_id, after_sequence=last_event_id or 0
            ):
                yield _sse_frame(event)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Run-ID": run_id},
        )

    return application


app = create_app()
