"""Phase 0 sanity tests — verify the package imports and the scaffolding is coherent.

Phase 1+ adds real tests per the phase plans. Phase 0 only verifies:

- The package imports cleanly
- The version string matches `pyproject.toml`
- The plugin.json + .mcp.json + hooks.json are valid JSON
- The LLM resolver can be imported and called without side effects
- The MCP server scaffold can be instantiated
- The CLI entry point is registered
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_package_imports() -> None:
    """The top-level package must import without errors."""
    import tailtest

    assert tailtest.__version__ == "0.1.0"


def test_version_matches_pyproject() -> None:
    """The package __version__ must match the pyproject.toml version."""
    import tomllib

    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    pyproject_version = pyproject["project"]["version"]

    import tailtest

    assert tailtest.__version__ == pyproject_version


def test_llm_resolver_imports() -> None:
    """The copied v1 LLM resolver must be importable."""
    from tailtest.llm.resolver import is_claude_code_available, resolve_judge_model

    # These are safe to call — they only probe the environment, no side effects.
    assert isinstance(is_claude_code_available(), bool)
    assert resolve_judge_model() is None or isinstance(resolve_judge_model(), str)


def test_llm_claude_cli_imports() -> None:
    """The copied v1 Claude CLI judge must be importable (uses Phase 0 stub)."""
    from tailtest.llm.claude_cli import ClaudeCodeJudge

    # Just construct it — don't actually call Claude.
    judge = ClaudeCodeJudge()
    assert judge is not None


def test_judge_stub_shape() -> None:
    """The Phase 0 judge stub must expose the types the Claude CLI wrapper imports."""
    from tailtest.core.assertions.llm_judge.judge import (
        JUDGE_SYSTEM_PROMPT,
        JudgeResult,
    )

    assert isinstance(JUDGE_SYSTEM_PROMPT, str)
    assert len(JUDGE_SYSTEM_PROMPT) > 0

    r = JudgeResult(passed=True, score=0.9, reason="ok")
    assert r.passed is True
    assert r.score == 0.9
    assert r.reason == "ok"


def test_mcp_server_instantiates() -> None:
    """The MCP server scaffold must instantiate without errors."""
    from tailtest.mcp.server import TailtestMCPServer

    server = TailtestMCPServer()
    assert server.initialized is False


def test_cli_main_importable() -> None:
    """The CLI entry point must be importable."""
    from tailtest.cli.main import main

    assert callable(main)


def test_plugin_manifest_valid_json() -> None:
    """The Claude Code plugin manifest must be valid JSON with required fields."""
    manifest_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["name"] == "tailtest"
    assert manifest["version"] == "0.1.0-rc.2"
    assert "description" in manifest
    assert manifest["license"] == "Apache-2.0"


def test_mcp_config_valid_json() -> None:
    """The .mcp.json must be valid JSON with the tailtest server wired."""
    mcp_config = json.loads((REPO_ROOT / ".mcp.json").read_text())

    assert "mcpServers" in mcp_config
    assert "tailtest" in mcp_config["mcpServers"]
    assert mcp_config["mcpServers"]["tailtest"]["command"] == "tailtest"
    assert mcp_config["mcpServers"]["tailtest"]["args"] == ["mcp-serve"]


def test_hooks_config_valid_json() -> None:
    """The hooks.json must be valid JSON with all three hooks registered."""
    hooks_config = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())

    assert "hooks" in hooks_config
    assert "PostToolUse" in hooks_config["hooks"]
    assert "SessionStart" in hooks_config["hooks"]
    assert "Stop" in hooks_config["hooks"]


def test_hook_scripts_exist_and_executable() -> None:
    """All three hook scripts must exist and be executable."""
    import os

    for script_name in ("post_tool_use.py", "session_start.py", "stop.py"):
        script = REPO_ROOT / "hooks" / script_name
        assert script.exists(), f"{script_name} missing"
        assert os.access(script, os.X_OK), f"{script_name} not executable"


def test_attacks_yaml_valid() -> None:
    """The red-team attacks.yaml must be valid YAML matching the schema version."""
    import yaml

    attacks_path = REPO_ROOT / "data" / "redteam" / "attacks.yaml"
    data = yaml.safe_load(attacks_path.read_text())

    assert data["schema_version"] == 1
    assert isinstance(data["attacks"], list)
    assert len(data["attacks"]) >= 1
    for attack in data["attacks"]:
        assert "id" in attack
        assert "category" in attack
        assert "payload" in attack
        assert "expected_outcome" in attack
        assert "severity_on_success" in attack
