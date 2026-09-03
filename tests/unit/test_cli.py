"""Developer CLI smoke tests."""

import json

import pytest

from dayu_agent.cli import main
from dayu_agent.config import get_settings


def set_fake_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure credential-free CLI behavior and suppress trace logs in assertions."""

    monkeypatch.setenv("DAYU_AGENT_ENV", "test")
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    monkeypatch.setenv("MODEL_NAME", "fake-cli")
    monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
    get_settings.cache_clear()


def test_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Version output must be stable and human-readable."""

    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == "dayu-water-agent 0.2.0"


def test_health_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Health must report provider readiness without making a model request."""

    set_fake_environment(monkeypatch)
    assert main(["health"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True


def test_single_chat_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-interactive chat must return the normalized AgentResult JSON."""

    set_fake_environment(monkeypatch)
    assert main(["chat", "--message", "hello"]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["content"] == "Fake response (turn 1): hello"
    assert response["metadata"]["provider"] == "fake"


def test_chat_error_is_safe_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI domain failures must be machine-readable and stack-trace free."""

    set_fake_environment(monkeypatch)
    assert main(["chat", "--message", "   "]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error_code"] == "GUARDRAIL_BLOCKED"
