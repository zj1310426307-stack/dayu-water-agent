"""Supervisor runtime tests across provider, session, guardrail, and tool paths."""

import pytest

from dayu_agent.agents import SupervisorAgent
from dayu_agent.exceptions import (
    GuardrailError,
    ProviderError,
    ToolNotFoundError,
    ToolValidationError,
)
from dayu_agent.memory import InMemorySessionStore, MessageRole
from dayu_agent.providers.base import ProviderResponse
from dayu_agent.providers.fake import FakeModelProvider
from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry


def make_supervisor(
    provider: FakeModelProvider | None = None,
) -> tuple[SupervisorAgent, InMemorySessionStore]:
    """Build a complete credential-free runtime for focused unit tests."""

    store = InMemorySessionStore()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return (
        SupervisorAgent(
            provider=provider or FakeModelProvider(),
            session_store=store,
            tool_registry=registry,
        ),
        store,
    )


@pytest.mark.asyncio
async def test_agent_creates_session_and_returns_normalized_result() -> None:
    """A normal turn must create and persist a two-message conversation."""

    supervisor, store = make_supervisor()
    result = await supervisor.run("hello", request_id="request-1")
    assert result.request_id == "request-1"
    assert result.content == "Fake response (turn 1): hello"
    assert result.metadata == {"provider": "fake", "model": "fake-deterministic"}
    messages = await store.list_messages(result.session_id)
    assert [message.role for message in messages] == [MessageRole.USER, MessageRole.ASSISTANT]


@pytest.mark.asyncio
async def test_same_session_supports_multiple_turns() -> None:
    """Provider history must contain both prior turns under one session identity."""

    supervisor, store = make_supervisor()
    session = await supervisor.create_session()
    first = await supervisor.run("first", session_id=session.id)
    second = await supervisor.run("second", session_id=session.id)
    assert first.session_id == second.session_id == session.id
    assert second.content == "Fake response (turn 2): second"
    assert len(await store.list_messages(session.id)) == 4


@pytest.mark.asyncio
async def test_explicit_tool_command_uses_registry_and_audit_record() -> None:
    """Safe explicit tool calls must traverse registry validation and appear in results."""

    supervisor, store = make_supervisor()
    result = await supervisor.run('/tool system.echo {"text":"water"}')
    assert result.content == '{"text":"water"}'
    assert result.tool_calls[0].name == "system.echo"
    assert result.tool_calls[0].output == {"text": "water"}
    assert len(await store.list_messages(result.session_id)) == 2


@pytest.mark.asyncio
async def test_unknown_tool_and_bad_json_fail_closed() -> None:
    """The Supervisor must not invent unknown tools or repair invalid payloads silently."""

    supervisor, _ = make_supervisor()
    with pytest.raises(ToolNotFoundError):
        await supervisor.run("/tool missing.tool {}")
    with pytest.raises(ToolValidationError, match="JSON object"):
        await supervisor.run("/tool system.echo not-json")


@pytest.mark.asyncio
async def test_empty_input_is_blocked_before_message_persistence() -> None:
    """Whitespace input must leave an existing conversation unchanged."""

    supervisor, store = make_supervisor()
    session = await supervisor.create_session()
    with pytest.raises(GuardrailError, match="empty"):
        await supervisor.run("   ", session_id=session.id)
    assert await store.list_messages(session.id) == ()


@pytest.mark.asyncio
async def test_provider_failure_propagates_as_safe_domain_error() -> None:
    """Provider failures must never become fabricated success responses."""

    supervisor, store = make_supervisor(FakeModelProvider(fail=True))
    session = await supervisor.create_session()
    with pytest.raises(ProviderError) as error:
        await supervisor.run("fail safely", session_id=session.id)
    assert "provider" in str(error.value.details)
    assert await store.list_messages(session.id) == ()


@pytest.mark.asyncio
async def test_empty_provider_output_is_blocked() -> None:
    """An empty provider response cannot be persisted as a successful answer."""

    provider = FakeModelProvider()

    async def empty_run(*_: object, **__: object) -> ProviderResponse:
        return ProviderResponse(content="")

    provider.run = empty_run  # type: ignore[method-assign]
    supervisor, store = make_supervisor(provider)
    session = await supervisor.create_session()
    with pytest.raises(GuardrailError, match="output"):
        await supervisor.run("question", session_id=session.id)
    assert await store.list_messages(session.id) == ()


@pytest.mark.asyncio
async def test_stream_forwards_native_delta_and_persists_final_result() -> None:
    """Streaming must emit provider events and persist exactly one assistant response."""

    supervisor, store = make_supervisor()
    events = [event async for event in supervisor.stream("stream this")]
    assert events[0].delta == "Fake response (turn 1): stream this"
    assert events[-1].done is True
    assert events[-1].result is not None
    messages = await store.list_messages(events[-1].result.session_id)
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_tool_command_stream_returns_only_final_event() -> None:
    """A deterministic tool result is not split into fake stream chunks."""

    supervisor, _ = make_supervisor()
    events = [event async for event in supervisor.stream("/tool system.health {}")]
    assert len(events) == 1
    assert events[0].done is True
    assert events[0].result is not None
    assert events[0].result.content == '{"status":"ok"}'
