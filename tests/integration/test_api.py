"""In-process HTTP tests for health, readiness, chat, sessions, streaming, and errors."""

import httpx
import pytest

from dayu_agent.api.app import create_app
from dayu_agent.config import Settings
from dayu_agent.providers.fake import FakeModelProvider


@pytest.mark.asyncio
async def test_health_and_ready_have_distinct_semantics(api_client: httpx.AsyncClient) -> None:
    """Liveness and provider readiness must be separate endpoints and payloads."""

    health = await api_client.get("/health")
    ready = await api_client.get("/ready")
    assert health.status_code == 200
    assert health.json()["status"] == "alive"
    assert "provider" not in health.json()
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "provider": "fake",
        "model": "fake-test",
        "detail": "deterministic provider ready",
        "components": {"runtime": True, "store": True, "provider": True},
    }
    assert health.headers["x-request-id"]


@pytest.mark.asyncio
async def test_session_and_multi_turn_chat(api_client: httpx.AsyncClient) -> None:
    """The API must preserve four ordered messages across two turns."""

    created = await api_client.post("/api/v1/sessions", json={"metadata": {"client": "test"}})
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    first = await api_client.post(
        "/api/v1/chat", json={"message": "one", "session_id": session_id}
    )
    second = await api_client.post(
        "/api/v1/chat", json={"message": "two", "session_id": session_id}
    )
    detail = await api_client.get(f"/api/v1/sessions/{session_id}")
    assert first.status_code == second.status_code == detail.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"] == session_id
    assert second.json()["content"] == "Fake response (turn 2): two"
    assert [message["content"] for message in detail.json()["messages"]] == [
        "one",
        "Fake response (turn 1): one",
        "two",
        "Fake response (turn 2): two",
    ]


@pytest.mark.asyncio
async def test_chat_can_create_an_implicit_session(api_client: httpx.AsyncClient) -> None:
    """A caller may omit session_id and receive a new stable identity."""

    response = await api_client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 200
    session_id = response.json()["session_id"]
    assert (await api_client.get(f"/api/v1/sessions/{session_id}")).status_code == 200


@pytest.mark.asyncio
async def test_tool_command_and_sse_stream(api_client: httpx.AsyncClient) -> None:
    """Tool and provider streams must use normalized result schemas and SSE events."""

    tool = await api_client.post(
        "/api/v1/chat", json={"message": '/tool system.echo {"text":"safe"}'}
    )
    assert tool.status_code == 200
    assert tool.json()["tool_calls"][0]["name"] == "system.echo"

    stream = await api_client.post("/api/v1/chat/stream", json={"message": "stream"})
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: response.delta" in stream.text
    assert "event: response.completed" in stream.text
    assert "id: 1" in stream.text
    assert stream.headers["x-run-id"]
    assert "Fake response (turn 1): stream" in stream.text


@pytest.mark.asyncio
async def test_domain_and_request_errors_use_uniform_safe_shape(
    api_client: httpx.AsyncClient,
) -> None:
    """Session, guardrail, and body validation errors must share the public contract."""

    missing = await api_client.get("/api/v1/sessions/missing")
    empty = await api_client.post("/api/v1/chat", json={"message": "   "})
    invalid = await api_client.post(
        "/api/v1/chat", json={"message": "valid", "unexpected": "denied"}
    )
    assert missing.status_code == 404
    assert missing.json()["error_code"] == "SESSION_NOT_FOUND"
    assert empty.status_code == 422
    assert empty.json()["error_code"] == "GUARDRAIL_BLOCKED"
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "REQUEST_VALIDATION_ERROR"
    for response in (missing, empty, invalid):
        body = response.json()
        assert set(body) == {"error_code", "message", "request_id", "details"}
        assert body["request_id"] == response.headers["x-request-id"]
        assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_provider_error_maps_to_502_without_raw_exception() -> None:
    """Model failures must cross the HTTP boundary as safe 502 errors."""

    settings = Settings(
        _env_file=None,
        environment="test",
        model_provider="fake",
        log_level="CRITICAL",
    )
    app = create_app(settings=settings, provider=FakeModelProvider(fail=True))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/chat", json={"message": "fail"})
    assert response.status_code == 502
    assert response.json()["error_code"] == "PROVIDER_ERROR"
    assert "stack" not in response.text.lower()


@pytest.mark.asyncio
async def test_openapi_contains_required_phase_zero_routes(api_client: httpx.AsyncClient) -> None:
    """The generated contract must expose every required Phase-01 endpoint."""

    schema = (await api_client.get("/openapi.json")).json()
    assert {
        "/health",
        "/ready",
        "/api/v1/chat",
        "/api/v1/chat/stream",
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/runs/{run_id}",
        "/api/v1/runs/{run_id}/cancel",
        "/api/v1/runs/{run_id}/stream",
    }.issubset(schema["paths"])


@pytest.mark.asyncio
async def test_http_idempotency_run_query_cancel_and_stream_resume(
    api_client: httpx.AsyncClient,
    fake_provider: FakeModelProvider,
) -> None:
    """Public runtime controls preserve one execution and resumable event identity."""

    headers = {"Idempotency-Key": "api-stable-key"}
    first = await api_client.post("/api/v1/chat", json={"message": "once"}, headers=headers)
    second = await api_client.post("/api/v1/chat", json={"message": "once"}, headers=headers)
    conflict = await api_client.post(
        "/api/v1/chat", json={"message": "changed"}, headers=headers
    )
    run_id = first.json()["run_id"]
    queried = await api_client.get(f"/api/v1/runs/{run_id}")
    cancelled = await api_client.post(f"/api/v1/runs/{run_id}/cancel")

    assert first.json() == second.json()
    assert fake_provider.call_count == 1
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"
    assert queried.json()["status"] == "completed"
    assert cancelled.json()["disposition"] == "already_terminal"

    streamed = await api_client.post(
        "/api/v1/chat/stream",
        json={"message": "resume"},
        headers={"Idempotency-Key": "api-stream-key"},
    )
    stream_run_id = streamed.headers["x-run-id"]
    resumed = await api_client.get(
        f"/api/v1/runs/{stream_run_id}/stream", headers={"Last-Event-ID": "1"}
    )
    assert "id: 1" not in resumed.text
    assert "event: response.delta" in resumed.text
    assert "event: response.completed" in resumed.text
