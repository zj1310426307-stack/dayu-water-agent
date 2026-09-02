"""Static assertions for Phase-00 architecture and safety boundaries."""

from pathlib import Path

from dayu_agent.tools.builtin import register_builtin_tools
from dayu_agent.tools.registry import ToolRegistry

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "src" / "dayu_agent"


def test_no_dayu_tiangong_import_or_prohibited_tool_modules() -> None:
    """The independent core must not import Tiangong or ship execution backdoors."""

    source = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE_ROOT.rglob("*.py"))
    assert "import dayu_tiangong" not in source
    assert "from dayu_tiangong" not in source
    prohibited_modules = {"shell.py", "sql.py", "filesystem.py", "python_exec.py"}
    assert not prohibited_modules.intersection(path.name for path in SOURCE_ROOT.rglob("*.py"))


def test_builtin_registry_has_no_side_effect_permissions() -> None:
    """Only two read-only deterministic tools may be available by default."""

    registry = ToolRegistry()
    register_builtin_tools(registry)
    assert {tool.name for tool in registry.list()} == {"system.health", "system.echo"}
    assert {tool.permission.value for tool in registry.list()} == {"READ"}
