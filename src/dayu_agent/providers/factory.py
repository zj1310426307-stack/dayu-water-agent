"""Construct the configured provider at the application boundary."""

from dayu_agent.config import Settings
from dayu_agent.exceptions import ConfigurationError
from dayu_agent.providers.base import ModelProvider
from dayu_agent.providers.fake import FakeModelProvider
from dayu_agent.providers.openai import OpenAIModelProvider


def build_provider(settings: Settings) -> ModelProvider:
    """Create exactly one concrete provider from validated settings."""

    if settings.model_provider == "fake":
        return FakeModelProvider(model_name=settings.model_name)
    if settings.model_provider == "openai" and settings.openai_api_key is not None:
        return OpenAIModelProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model_name=settings.model_name,
            timeout_seconds=settings.provider_timeout_seconds,
            sdk_tracing_enabled=settings.sdk_tracing_enabled,
        )
    raise ConfigurationError(details={"model_provider": settings.model_provider})
