"""Fake and OpenAI Agents SDK provider-adapter tests without network calls."""

from types import SimpleNamespace
from typing import Any

import pytest

from dayu_agent.exceptions import ProviderError
from dayu_agent.providers.base import MessageRole, ProviderMessage, ProviderRequest
from dayu_agent.providers.fake import FakeModelProvider
from dayu_agent.providers.openai import OpenAIModelProvider
from dayu_agent.runtime.context import AgentContext


def provider_request() -> ProviderRequest:
    """Create one valid provider-neutral request."""

    return ProviderRequest(
        context=AgentContext(session_id="session", request_id="request"),
        messages=(ProviderMessage(role=MessageRole.USER, content="hello"),),
    )


def fake_sdk_result(content: str = "sdk answer") -> SimpleNamespace:
    """Build the subset of an Agents SDK result consumed by the adapter."""

    usage = SimpleNamespace(requests=1, input_tokens=2, output_tokens=3, total_tokens=5)
    return SimpleNamespace(
        final_output=content,
        context_wrapper=SimpleNamespace(usage=usage),
        new_items=[],
    )


@pytest.mark.asyncio
async def test_fake_provider_health_run_and_stream() -> None:
    """The fake implementation must satisfy the full provider contract."""

    provider = FakeModelProvider()
    assert (await provider.health()).ready is True
    response = await provider.run(provider_request())
    assert response.content.endswith("hello")
    events = [event async for event in provider.stream(provider_request())]
    assert events[0].delta is not None
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_openai_provider_uses_agents_runner_and_normalizes_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OpenAI adapter must use Runner while returning only local contracts."""

    async def fake_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        assert args
        assert kwargs["run_config"].trace_include_sensitive_data is False
        return fake_sdk_result()

    monkeypatch.setattr("dayu_agent.providers.openai.Runner.run", fake_run)
    provider = OpenAIModelProvider(
        api_key="test-key",
        model_name="gpt-5.6",
        timeout_seconds=1,
        sdk_tracing_enabled=False,
    )
    assert provider._client.max_retries == 0
    assert (await provider.health()).provider == "openai"
    response = await provider.run(provider_request())
    assert response.content == "sdk answer"
    assert response.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_openai_provider_normalizes_sdk_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw SDK exceptions must not cross the provider boundary."""

    async def failing_run(*_: Any, **__: Any) -> SimpleNamespace:
        raise RuntimeError("raw sdk secret")

    monkeypatch.setattr("dayu_agent.providers.openai.Runner.run", failing_run)
    provider = OpenAIModelProvider(
        api_key="test-key",
        model_name="gpt-5.6",
        timeout_seconds=1,
        sdk_tracing_enabled=False,
    )
    with pytest.raises(ProviderError) as error:
        await provider.run(provider_request())
    assert "raw sdk secret" not in str(error.value)


@pytest.mark.asyncio
async def test_openai_provider_forwards_stream_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    """The adapter must forward SDK deltas and emit one normalized final event."""

    class FakeStreamingResult(SimpleNamespace):
        """Expose the SDK stream iterator used by the adapter."""

        async def stream_events(self) -> Any:
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="sdk "),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="answer"),
            )

    streaming_result = FakeStreamingResult(**fake_sdk_result().__dict__)
    monkeypatch.setattr(
        "dayu_agent.providers.openai.Runner.run_streamed",
        lambda *args, **kwargs: streaming_result,
    )
    provider = OpenAIModelProvider(
        api_key="test-key",
        model_name="gpt-5.6",
        timeout_seconds=1,
        sdk_tracing_enabled=False,
    )
    events = [event async for event in provider.stream(provider_request())]
    assert "".join(event.delta or "" for event in events) == "sdk answer"
    assert events[-1].done is True


@pytest.mark.asyncio
async def test_openai_provider_rejects_empty_final_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty SDK result is a controlled provider failure."""

    async def fake_run(*_: Any, **__: Any) -> SimpleNamespace:
        return fake_sdk_result("")

    monkeypatch.setattr("dayu_agent.providers.openai.Runner.run", fake_run)
    provider = OpenAIModelProvider(
        api_key="test-key",
        model_name="gpt-5.6",
        timeout_seconds=1,
        sdk_tracing_enabled=False,
    )
    with pytest.raises(ProviderError, match="empty"):
        await provider.run(provider_request())
