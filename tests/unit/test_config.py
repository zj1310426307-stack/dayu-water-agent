"""Configuration validation and override tests."""

import os

import pytest
from pydantic import ValidationError

from dayu_agent.config import Settings


@pytest.mark.parametrize(
    "variable",
    ("DATABASE_URL", "MODEL_PROVIDER", "SESSION_STORE", "OPENAI_API_KEY"),
)
def test_settings_tests_clear_ambient_runtime_configuration(variable: str) -> None:
    """The autouse fixture must remove host configuration before each test."""

    assert variable not in os.environ


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
    """Readiness diagnostics must omit credentials and database URLs entirely."""

    settings = Settings(
        _env_file=None,
        model_provider="openai",
        openai_api_key="secret-value",
    )
    summary = settings.safe_summary()
    assert "openai_api_key" not in summary
    assert "database_url" not in summary
    assert "secret-value" not in str(summary)


def test_production_requires_explicit_postgres_store_and_url() -> None:
    """Production cannot silently fall back to process-local session state."""

    with pytest.raises(ValidationError, match="SESSION_STORE=postgres"):
        Settings(_env_file=None, environment="production", session_store="memory")
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None, environment="production", session_store="postgres")
    settings = Settings(
        _env_file=None,
        environment="production",
        session_store="postgres",
        database_url="postgresql+psycopg://user:password@database/dayu",
    )
    assert settings.session_store == "postgres"
