"""Shared deterministic fixtures for unit and API tests."""

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio

from dayu_agent.api.app import create_app
from dayu_agent.config import Settings, get_settings
from dayu_agent.providers.fake import FakeModelProvider

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


_SETTINGS_ENVIRONMENT_VARIABLES = (
    "DAYU_AGENT_ENV",
    "DAYU_AGENT_HOST",
    "DAYU_AGENT_PORT",
    "MODEL_PROVIDER",
    "MODEL_NAME",
    "OPENAI_API_KEY",
    "SESSION_STORE",
    "DATABASE_URL",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT_SECONDS",
    "LOG_LEVEL",
    "PROVIDER_TIMEOUT_SECONDS",
    "SDK_TRACING_ENABLED",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_MAX_ELAPSED_SECONDS",
    "RETRY_BASE_DELAY_SECONDS",
    "RETRY_MAX_DELAY_SECONDS",
    "RETRY_JITTER",
    "STREAM_EVENT_RETENTION_SECONDS",
    "MAX_REQUEST_BYTES",
    "OTEL_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
)


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make every test independent from ambient runtime configuration."""

    for variable in _SETTINGS_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_settings() -> Settings:
    """Return credential-free test configuration."""

    return Settings(
        _env_file=None,
        environment="test",
        model_provider="fake",
        model_name="fake-test",
        log_level="CRITICAL",
        database_url="sqlite+pysqlite:///:memory:",
    )


@pytest.fixture
def fake_provider() -> FakeModelProvider:
    """Return a fresh deterministic provider for isolation."""

    return FakeModelProvider(model_name="fake-test")


@pytest_asyncio.fixture
async def api_client(
    test_settings: Settings,
    fake_provider: FakeModelProvider,
) -> AsyncIterator[httpx.AsyncClient]:
    """Serve the FastAPI application in-process through HTTPX ASGI transport."""

    app = create_app(settings=test_settings, provider=fake_provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
