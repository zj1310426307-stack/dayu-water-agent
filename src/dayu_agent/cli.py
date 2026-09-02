"""Small dependency-free developer CLI for health checks and chat."""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence

from dayu_agent import __version__
from dayu_agent.api.app import build_container
from dayu_agent.config import get_settings
from dayu_agent.exceptions import DayuAgentError
from dayu_agent.observability import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """Create the stable CLI command surface."""

    parser = argparse.ArgumentParser(prog="dayu-agent", description="Dayu Water Agent CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("version", help="Show the installed version")
    subcommands.add_parser("health", help="Check local runtime readiness")
    chat = subcommands.add_parser("chat", help="Run one message or enter interactive chat")
    chat.add_argument("--message", help="Send one message without entering the interactive loop")
    chat.add_argument("--session-id", help="Continue an existing process-local session")
    return parser


async def _health_command() -> int:
    """Check configuration-level readiness without making a model request."""

    settings = get_settings()
    container = build_container(settings)
    health = await container.provider.health()
    sys.stdout.write(health.model_dump_json() + "\n")
    return 0 if health.ready else 1


async def _single_chat(message: str, session_id: str | None) -> int:
    """Run one chat turn and write the normalized result as JSON."""

    settings = get_settings()
    container = build_container(settings)
    result = await container.supervisor.run(message, session_id=session_id)
    sys.stdout.write(result.model_dump_json() + "\n")
    return 0


async def _interactive_chat(session_id: str | None) -> int:
    """Run a minimal interactive loop with one in-process session."""

    settings = get_settings()
    container = build_container(settings)
    active_session = session_id
    if active_session is None:
        active_session = (await container.supervisor.create_session()).id

    sys.stdout.write("Dayu Water Agent\nType 'exit' or 'quit' to stop.\n\n")
    while True:
        try:
            message = (await asyncio.to_thread(input, "> ")).strip()
        except EOFError:
            sys.stdout.write("\n")
            return 0
        if message.lower() in {"exit", "quit"}:
            return 0
        if not message:
            continue
        result = await container.supervisor.run(message, session_id=active_session)
        sys.stdout.write(result.content + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch a CLI command and map domain errors to safe stderr JSON."""

    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
        configure_logging(settings.log_level)
        if args.command == "version":
            sys.stdout.write(f"dayu-water-agent {__version__}\n")
            return 0
        if args.command == "health":
            return asyncio.run(_health_command())
        if args.command == "chat":
            if args.message is not None:
                return asyncio.run(_single_chat(args.message, args.session_id))
            return asyncio.run(_interactive_chat(args.session_id))
        raise AssertionError("argparse accepted an unknown command")
    except DayuAgentError as exc:
        payload = {
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        }
        sys.stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 1
