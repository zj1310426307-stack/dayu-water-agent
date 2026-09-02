"""Generate or verify the committed FastAPI OpenAPI contract."""

import argparse
import json
from pathlib import Path

from dayu_agent.api.app import create_app
from dayu_agent.config import Settings

OUTPUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def rendered_contract() -> str:
    """Return deterministic JSON from a credential-free application graph."""

    application = create_app(
        settings=Settings(
            _env_file=None,
            environment="test",
            model_provider="fake",
            model_name="fake-openapi",
            session_store="memory",
        )
    )
    return json.dumps(
        application.openapi(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    """Write the contract or fail when the committed artifact is stale."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = rendered_contract()
    if args.check:
        return 0 if OUTPUT_PATH.exists() and OUTPUT_PATH.read_text("utf-8") == rendered else 1
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
