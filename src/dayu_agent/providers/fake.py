"""Deterministic provider used by tests, CI, and credential-free development."""

import asyncio
from collections.abc import AsyncIterator

from dayu_agent.exceptions import ProviderError, ProviderUnavailableError
from dayu_agent.providers.base import (
    MessageRole,
    ModelProvider,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamEvent,
)
from dayu_agent.runtime.result import TokenUsage


class FakeModelProvider(ModelProvider):
    """Return deterministic text without network access or token charges."""

    def __init__(
        self,
        *,
        model_name: str = "fake-deterministic",
        fail: bool = False,
        fail_times: int = 0,
        failure_retryable: bool = False,
        delay_seconds: float = 0,
        stream_chunks: tuple[str, ...] | None = None,
        stream_fail_after_deltas: int | None = None,
        block_event: asyncio.Event | None = None,
    ) -> None:
        """Configure deterministic delays, failures, chunks, and cancellation points."""

        self._model_name = model_name
        self._fail = fail
        self._fail_times = fail_times
        self._failure_retryable = failure_retryable
        self._delay_seconds = delay_seconds
        self._stream_chunks = stream_chunks
        self._stream_fail_after_deltas = stream_fail_after_deltas
        self._block_event = block_event
        self.call_count = 0

    @property
    def name(self) -> str:
        """Return the provider identifier."""

        return "fake"

    @property
    def model_name(self) -> str:
        """Return the deterministic model identifier."""

        return self._model_name

    async def health(self) -> ProviderHealth:
        """Fake provider is ready whenever it has been constructed."""

        return ProviderHealth(
            ready=True,
            provider=self.name,
            model=self.model_name,
            detail="deterministic provider ready",
        )

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        """Build a stable response from the latest user message and turn number."""

        self.call_count += 1
        await self._wait()
        self._raise_scripted_failure()
        return self._response(request)

    async def _wait(self) -> None:
        """Apply cancellable scripted latency and optional external blocking."""

        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._block_event is not None:
            await self._block_event.wait()

    def _raise_scripted_failure(self) -> None:
        """Raise the configured stable failure for the current call number."""

        if not self._fail and self.call_count > self._fail_times:
            return
        details = {"provider": self.name, "model": self.model_name}
        if self._failure_retryable:
            raise ProviderUnavailableError(details=details)
        raise ProviderError(details=details)

    def _response(self, request: ProviderRequest) -> ProviderResponse:
        """Construct one deterministic normalized response without side effects."""

        user_messages = [item.content for item in request.messages if item.role is MessageRole.USER]
        latest_message = user_messages[-1]
        content = f"Fake response (turn {len(user_messages)}): {latest_message}"
        input_tokens = sum(len(item.content.split()) for item in request.messages)
        output_tokens = len(content.split())
        return ProviderResponse(
            content=content,
            usage=TokenUsage(
                requests=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderStreamEvent]:
        """Yield scripted chunks and optionally fail after output has started."""

        self.call_count += 1
        await self._wait()
        self._raise_scripted_failure()
        response = self._response(request)
        chunks = self._stream_chunks or (response.content,)
        for index, chunk in enumerate(chunks, start=1):
            yield ProviderStreamEvent(delta=chunk)
            if self._stream_fail_after_deltas == index:
                details = {"provider": self.name, "model": self.model_name}
                if self._failure_retryable:
                    raise ProviderUnavailableError(details=details)
                raise ProviderError(details=details)
        yield ProviderStreamEvent(response=response, done=True)
