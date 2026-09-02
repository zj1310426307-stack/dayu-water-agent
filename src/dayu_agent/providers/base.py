"""Provider-neutral model contracts."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dayu_agent.runtime.context import AgentContext
from dayu_agent.runtime.result import TokenUsage


class MessageRole(StrEnum):
    """Roles accepted by the provider boundary."""

    USER = "user"
    ASSISTANT = "assistant"


class ProviderMessage(BaseModel):
    """Minimal conversation message understood by all providers."""

    model_config = ConfigDict(frozen=True)

    role: MessageRole
    content: str


class ProviderRequest(BaseModel):
    """Complete normalized input for one provider invocation."""

    model_config = ConfigDict(frozen=True)

    context: AgentContext
    messages: tuple[ProviderMessage, ...] = Field(min_length=1)


class ProviderResponse(BaseModel):
    """Normalized result from a non-streaming provider invocation."""

    model_config = ConfigDict(frozen=True)

    content: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_calls: tuple[str, ...] = ()


class ProviderStreamEvent(BaseModel):
    """One native provider stream event or final normalized response."""

    model_config = ConfigDict(frozen=True)

    delta: str | None = None
    response: ProviderResponse | None = None
    done: bool = False


class ProviderHealth(BaseModel):
    """Readiness information that never exposes credentials."""

    model_config = ConfigDict(frozen=True)

    ready: bool
    provider: str
    model: str
    detail: str


class ModelProvider(ABC):
    """Replaceable model boundary used by the Supervisor runtime."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the stable provider identifier."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the configured model identifier."""

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Report configuration-level provider readiness without consuming tokens."""

    @abstractmethod
    async def run(self, request: ProviderRequest) -> ProviderResponse:
        """Execute one complete provider turn."""

    @abstractmethod
    def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderStreamEvent]:
        """Yield provider-native deltas and exactly one final event."""
