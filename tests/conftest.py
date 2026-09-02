"""Shared deterministic fixtures for unit and API tests."""

import asyncio
import sys
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

from dayu_agent.api.app import create_app
from dayu_agent.config import Settings, get_settings
from dayu_agent.providers.fake import FakeModelProvider

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Prevent environment-backed settings from leaking between tests."""

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
