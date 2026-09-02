"""Configuration validation and override tests."""

import pytest
from pydantic import ValidationError

from dayu_agent.config import Settings


def test_fake_provider_requires_no_secret() -> None:
    """Default development configuration must work without an API key."""

    settings = Settings(_env_file=None)
    assert settings.model_provider == "fake"
    assert settings.openai_api_key is None


def test_openai_provider_requires_api_key() -> None:
    """Selecting OpenAI without credentials must fail during configuration load."""

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, model_provider="openai", openai_api_key=None)


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables must override default host, port, and log level."""

    monkeypatch.setenv("DAYU_AGENT_ENV", "test")
    monkeypatch.setenv("DAYU_AGENT_HOST", "127.0.0.1")
    monkeypatch.setenv("DAYU_AGENT_PORT", "9123")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    settings = Settings(_env_file=None)
    assert settings.environment == "test"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9123
    assert settings.log_level == "WARNING"


def test_safe_summary_never_contains_api_key() -> None:
    """Readiness diagnostics must omit the secret field entirely."""

    settings = Settings(
        _env_file=None,
        model_provider="openai",
        openai_api_key="secret-value",
    )
    summary = settings.safe_summary()
    assert "openai_api_key" not in summary
    assert "secret-value" not in str(summary)
