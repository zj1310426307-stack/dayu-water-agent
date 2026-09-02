"""OpenAI Agents SDK implementation of the model provider boundary."""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from agents import Agent, OpenAIProvider, RunConfig, Runner
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)

from dayu_agent.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from dayu_agent.providers.base import (
    ModelProvider,
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    ProviderStreamEvent,
)
from dayu_agent.runtime.result import TokenUsage

logger = logging.getLogger(__name__)

SUPERVISOR_INSTRUCTIONS = """You are Dayu Water Agent's foundation-stage Supervisor.
Answer clearly and conservatively. You do not have hydraulic, GIS, flood-evaluation,
database, shell, filesystem, or automatic-control capabilities. Never claim that an
unavailable capability has been executed. Ask for evidence when an engineering claim
cannot be supported by the provided conversation."""


class OpenAIModelProvider(ModelProvider):
    """Run a single focused Supervisor through the official OpenAI Agents SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        timeout_seconds: float,
        sdk_tracing_enabled: bool,
    ) -> None:
        """Build a zero-retry SDK client so the Runtime owns the only retry budget."""

        self._model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._client = AsyncOpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=timeout_seconds,
        )
        self._sdk_provider = OpenAIProvider(
            openai_client=self._client,
            use_responses=True,
        )
        self._run_config = RunConfig(
            model_provider=self._sdk_provider,
            tracing_disabled=not sdk_tracing_enabled,
            workflow_name="Dayu Water Agent Supervisor",
            trace_include_sensitive_data=False,
        )
        self._agent: Agent[None] = Agent(
            name="SupervisorAgent",
            instructions=SUPERVISOR_INSTRUCTIONS,
            model=model_name,
        )

    @property
    def name(self) -> str:
        """Return the provider identifier."""

        return "openai"

    @property
    def model_name(self) -> str:
        """Return the configured explicit model."""

        return self._model_name

    async def health(self) -> ProviderHealth:
        """Report validated configuration without spending tokens on a network probe."""

        return ProviderHealth(
            ready=True,
            provider=self.name,
            model=self.model_name,
            detail="configuration valid; live model call not probed",
        )

    def _input_items(self, request: ProviderRequest) -> list[dict[str, str]]:
        """Convert provider messages to the SDK Responses input shape."""

        return [
            {"role": message.role.value, "content": message.content}
            for message in request.messages
        ]

    def _response_from_result(self, result: Any) -> ProviderResponse:
        """Normalize an SDK run result while keeping SDK objects inside this provider."""

        usage = result.context_wrapper.usage
        tool_calls = tuple(
            str(getattr(getattr(item, "raw_item", None), "name", "unknown"))
            for item in result.new_items
            if getattr(item, "type", "") == "tool_call_item"
        )
        return ProviderResponse(
            content=str(result.final_output or ""),
            usage=TokenUsage(
                requests=usage.requests,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
            ),
            tool_calls=tool_calls,
        )

    def _normalize_failure(self, exc: Exception) -> ProviderError:
        """Map SDK failures to stable retryable or terminal provider errors."""

        details = {"provider": self.name, "model": self.model_name}
        if isinstance(exc, (TimeoutError, APITimeoutError)):
            return ProviderTimeoutError(details=details)
        if isinstance(
            exc,
            (APIConnectionError, RateLimitError, InternalServerError),
        ):
            return ProviderUnavailableError(details=details)
        if isinstance(exc, APIStatusError) and (
            exc.status_code >= 500 or exc.status_code in {408, 409, 429}
        ):
            return ProviderUnavailableError(details=details)
        return ProviderError(details=details)

    async def run(self, request: ProviderRequest) -> ProviderResponse:
        """Execute a complete SDK run with a provider-owned timeout and client."""

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = await Runner.run(
                    self._agent,
                    cast(Any, self._input_items(request)),
                    run_config=self._run_config,
                )
            response = self._response_from_result(result)
            if not response.content.strip():
                raise ProviderError("The model provider returned empty content.")
            return response
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            logger.error(
                "OpenAI provider run failed",
                extra={
                    "request_id": request.context.request_id,
                    "provider": self.name,
                    "model": self.model_name,
                    "error": type(exc).__name__,
                },
            )
            raise self._normalize_failure(exc) from exc

    async def stream(self, request: ProviderRequest) -> AsyncIterator[ProviderStreamEvent]:
        """Forward native Agents SDK text deltas and emit one normalized final event."""

        try:
            async with asyncio.timeout(self._timeout_seconds):
                result = Runner.run_streamed(
                    self._agent,
                    cast(Any, self._input_items(request)),
                    run_config=self._run_config,
                )
                async for event in result.stream_events():
                    if event.type != "raw_response_event":
                        continue
                    data = event.data
                    if getattr(data, "type", "") != "response.output_text.delta":
                        continue
                    delta = str(getattr(data, "delta", ""))
                    if delta:
                        yield ProviderStreamEvent(delta=delta)

                response = self._response_from_result(result)
                if not response.content.strip():
                    raise ProviderError("The model provider returned empty content.")
                yield ProviderStreamEvent(response=response, done=True)
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            logger.error(
                "OpenAI provider stream failed",
                extra={
                    "request_id": request.context.request_id,
                    "provider": self.name,
                    "model": self.model_name,
                    "error": type(exc).__name__,
                },
            )
            raise self._normalize_failure(exc) from exc
