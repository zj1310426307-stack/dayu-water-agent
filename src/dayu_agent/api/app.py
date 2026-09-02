"""FastAPI transport layer for the independent Supervisor runtime."""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse

from dayu_agent import __version__
from dayu_agent.agents import SupervisorAgent
from dayu_agent.api.schemas import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    ReadyResponse,
    SessionCreateRequest,
    SessionDetailResponse,
    SessionResponse,
)
from dayu_agent.config import Settings, get_settings
from dayu_agent.exceptions import DayuAgentError
from dayu_agent.memory import InMemorySessionStore, SessionStore
from dayu_agent.observability import configure_logging
from dayu_agent.providers import ModelProvider, build_provider
from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Own runtime dependencies constructed at the API boundary."""

    settings: Settings
    provider: ModelProvider
    session_store: SessionStore
    tool_registry: ToolRegistry
    supervisor: SupervisorAgent


def build_container(
    settings: Settings,
    *,
    provider: ModelProvider | None = None,
    session_store: SessionStore | None = None,
    tool_registry: ToolRegistry | None = None,
) -> ApplicationContainer:
    """Construct an explicit dependency graph suitable for tests and production."""

    selected_provider = provider or build_provider(settings)
    selected_store = session_store or InMemorySessionStore()
    selected_registry = tool_registry or ToolRegistry()
    if not selected_registry.list():
        register_builtin_tools(selected_registry)
    supervisor = SupervisorAgent(
        provider=selected_provider,
        session_store=selected_store,
        tool_registry=selected_registry,
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
    """Build the only error shape returned by the HTTP transport."""

    payload = ErrorResponse(
        error_code=error_code,
        message=message,
        request_id=request_id,
        details=details,
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    session_store: SessionStore | None = None,
    tool_registry: ToolRegistry | None = None,
) -> FastAPI:
    """Create an injectable application with no database startup dependency."""

    selected_settings = settings or get_settings()
    configure_logging(selected_settings.log_level)
    container = build_container(
        selected_settings,
        provider=provider,
        session_store=session_store,
        tool_registry=tool_registry,
    )
    application = FastAPI(
        title="Dayu Water Agent API",
        version=__version__,
        description="Independent AGENT-PHASE-00 runtime foundation.",
    )
    application.state.container = container

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Attach a generated correlation identity to every request and response."""

        request.state.request_id = str(uuid4())
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
        """Normalize FastAPI/Pydantic request validation failures."""

        return _error_response(
            status_code=422,
            error_code="REQUEST_VALIDATION_ERROR",
            message="The request body failed validation.",
            request_id=_request_id(request),
            details={"errors": _safe_validation_details(exc)},
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Fail closed with a generic response while logging only the exception type."""

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
        """Report process liveness only."""

        return HealthResponse(status="alive", service="dayu-water-agent", version=__version__)

    @application.get(
        "/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ErrorResponse}},
    )
    async def ready(request: Request) -> ReadyResponse | JSONResponse:
        """Report runtime and provider configuration readiness without live token use."""

        provider_health = await container.provider.health()
        if not provider_health.ready:
            return _error_response(
                status_code=503,
                error_code="NOT_READY",
                message="The agent runtime is not ready.",
                request_id=_request_id(request),
                details=provider_health.model_dump(mode="json"),
            )
        return ReadyResponse(
            status="ready",
            provider=provider_health.provider,
            model=provider_health.model,
            detail=provider_health.detail,
        )

    @application.post(
        "/api/v1/sessions",
        response_model=SessionResponse,
        status_code=201,
    )
    async def create_session(payload: SessionCreateRequest) -> SessionResponse:
        """Create a process-local session through the storage contract."""

        session = await container.supervisor.create_session(payload.metadata)
        return SessionResponse(
            session_id=session.id,
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @application.get(
        "/api/v1/sessions/{session_id}",
        response_model=SessionDetailResponse,
    )
    async def get_session(session_id: str) -> SessionDetailResponse:
        """Return session metadata and ordered history through the abstract store."""

        session = await container.session_store.get_session(session_id)
        messages = await container.session_store.list_messages(session_id)
        return SessionDetailResponse(
            session_id=session.id,
            metadata=session.metadata,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages=messages,
        )

    @application.post("/api/v1/chat", response_model=ChatResponse)
    async def chat(payload: ChatRequest, request: Request) -> ChatResponse:
        """Run one non-streaming Supervisor turn."""

        result = await container.supervisor.run(
            payload.message,
            session_id=payload.session_id,
            user_id=payload.user_id,
            metadata=payload.metadata,
            request_id=_request_id(request),
        )
        return ChatResponse.model_validate(result.model_dump())

    @application.post("/api/v1/chat/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        """Return SSE events sourced from the provider's native stream."""

        request_id = _request_id(request)

        async def event_source() -> AsyncIterator[str]:
            """Serialize normalized stream events and safe failures as SSE frames."""

            try:
                async for event in container.supervisor.stream(
                    payload.message,
                    session_id=payload.session_id,
                    user_id=payload.user_id,
                    metadata=payload.metadata,
                    request_id=request_id,
                ):
                    if event.delta is not None:
                        data = json.dumps({"delta": event.delta}, ensure_ascii=False)
                        yield f"event: delta\ndata: {data}\n\n"
                    if event.done and event.result is not None:
                        data = event.result.model_dump_json()
                        yield f"event: done\ndata: {data}\n\n"
            except DayuAgentError as exc:
                error = ErrorResponse(
                    error_code=exc.error_code,
                    message=exc.message,
                    request_id=request_id,
                    details=exc.details,
                )
                yield f"event: error\ndata: {error.model_dump_json()}\n\n"

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Request-ID": request_id},
        )

    return application


app = create_app()
