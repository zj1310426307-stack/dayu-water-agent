"""Explicitly opt-in live provider smoke test."""

import os

import pytest

from dayu_agent.providers.base import MessageRole, ProviderMessage, ProviderRequest
from dayu_agent.providers.openai import OpenAIModelProvider
from dayu_agent.runtime.context import AgentContext

LIVE_ENABLED = os.getenv("RUN_OPENAI_INTEGRATION") == "1" and bool(os.getenv("OPENAI_API_KEY"))


@pytest.mark.integration
@pytest.mark.skipif(not LIVE_ENABLED, reason="live OpenAI integration is opt-in")
@pytest.mark.asyncio
async def test_live_openai_provider() -> None:
    """Make one minimal live call only when the operator explicitly opts in."""

    provider = OpenAIModelProvider(
        api_key=os.environ["OPENAI_API_KEY"],
        model_name=os.getenv("MODEL_NAME", "gpt-5.6"),
        timeout_seconds=60,
        sdk_tracing_enabled=False,
    )
    response = await provider.run(
        ProviderRequest(
            context=AgentContext(session_id="integration"),
            messages=(ProviderMessage(role=MessageRole.USER, content="Reply with: ok"),),
        )
    )
    assert response.content.strip()
