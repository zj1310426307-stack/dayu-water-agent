"""Deterministic provider used by tests, CI, and credential-free development."""

from collections.abc import AsyncIterator

from dayu_agent.exceptions import ProviderError
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

    def __init__(self, *, model_name: str = "fake-deterministic", fail: bool = False) -> None:
        self._model_name = model_name
        self._fail = fail
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
        if self._fail:
            raise ProviderError(details={"provider": self.name, "model": self.model_name})

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
        """Yield the deterministic provider result as one native logical delta."""

        response = await self.run(request)
        yield ProviderStreamEvent(delta=response.content)
        yield ProviderStreamEvent(response=response, done=True)
