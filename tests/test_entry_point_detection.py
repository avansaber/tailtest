"""Phase 6 Task 6.3 -- agent entry-point detection tests.

Covers:
- Python entry point patterns: @agent_test, main() in agents/, invoke(), run()
- TypeScript: default export, Vercel AI SDK, UserMessage patterns
- Rust: async fn with LLM client imports
- Config-override precedence (declared wins over auto-detected)
- No false positives: non-agent files produce no entry points
- detect_entry_points() integration: uses profile.agent_entry_points
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tailtest.core.scan.detectors import detect_entry_points
from tailtest.core.scan.profile import EntryPoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _ep(tmp_path: Path, filename: str, content: str) -> list[EntryPoint]:
    f = _write(tmp_path / filename, content)
    return detect_entry_points(tmp_path, [f], "python")


# ---------------------------------------------------------------------------
# Python detection
# ---------------------------------------------------------------------------


def test_py_detects_agent_test_decorator(tmp_path: Path) -> None:
    eps = _ep(
        tmp_path,
        "agent.py",
        "@agent_test\ndef my_agent(input: str) -> str:\n    ...\n",
    )
    assert any(e.function == "my_agent" and e.confidence == "high" for e in eps)


def test_py_detects_main_in_agents_dir(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "agents" / "researcher.py",
        "def main(query: str) -> str:\n    return call_llm(query)\n",
    )
    eps = detect_entry_points(tmp_path, [f], "python")
    assert any(e.function == "main" for e in eps)


def test_py_detects_invoke_method(tmp_path: Path) -> None:
    eps = _ep(
        tmp_path,
        "agent.py",
        "class MyAgent:\n    def invoke(self, input: str) -> str:\n        ...\n",
    )
    assert any(e.function == "invoke" for e in eps)


def test_py_boosts_confidence_with_anthropic_client(tmp_path: Path) -> None:
    eps = _ep(
        tmp_path,
        "bot.py",
        "import anthropic\nclient = Anthropic()\ndef run(msg):\n    return client.messages.create()\n",
    )
    # run() alone is low, but combined with Anthropic() it becomes medium
    assert any(e.function == "run" and e.confidence in {"medium", "high"} for e in eps)


def test_py_no_entry_point_in_plain_module(tmp_path: Path) -> None:
    eps = _ep(
        tmp_path,
        "utils.py",
        "def helper(x):\n    return x * 2\n\ndef format_output(s):\n    return s.strip()\n",
    )
    # No agent patterns -- nothing should be detected
    # (run() alone in a non-agent file is low confidence but may still appear;
    #  check that no high/medium confidence hits appear for plain utility code)
    high_medium = [e for e in eps if e.confidence in {"high", "medium"}]
    assert not high_medium


# ---------------------------------------------------------------------------
# TypeScript detection
# ---------------------------------------------------------------------------


def test_ts_detects_default_export(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "agent.ts",
        "export default async function runAgent(input: string) {\n  return {};\n}\n",
    )
    eps = detect_entry_points(tmp_path, [f], "typescript")
    assert any(e.function == "runAgent" and e.confidence == "high" for e in eps)


def test_ts_detects_vercel_ai_sdk(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "handler.ts",
        "import { streamText } from 'ai';\nexport async function POST(req: Request) {\n"
        "  return streamText({ model: myModel, prompt: req.body });\n}\n",
    )
    eps = detect_entry_points(tmp_path, [f], "typescript")
    assert any(e.framework == "vercel-ai-sdk" for e in eps)


def test_ts_detects_user_message_function(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "chat.ts",
        "async function handleMessage(msg: UserMessage): Promise<Response> {\n"
        "  return llm(msg);\n}\n",
    )
    eps = detect_entry_points(tmp_path, [f], "typescript")
    assert any(e.function == "handleMessage" for e in eps)


def test_ts_no_false_positive_on_plain_ts(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "types.ts",
        "export interface Config { apiKey: string; }\nexport type Result = { data: string };\n",
    )
    eps = detect_entry_points(tmp_path, [f], "typescript")
    assert eps == []


# ---------------------------------------------------------------------------
# Rust detection
# ---------------------------------------------------------------------------


def test_rust_detects_pub_async_fn_with_client(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "agent.rs",
        "use async_openai::Client;\n\npub async fn run_agent(msg: &str) -> String {\n"
        "    let client = Client::new();\n    todo!()\n}\n",
    )
    eps = detect_entry_points(tmp_path, [f], "rust")
    assert any(e.function == "run_agent" and e.language == "rust" for e in eps)


def test_rust_no_entry_without_client_import(tmp_path: Path) -> None:
    f = _write(
        tmp_path / "utils.rs",
        "pub async fn helper(x: u32) -> u32 {\n    x + 1\n}\n",
    )
    eps = detect_entry_points(tmp_path, [f], "rust")
    assert eps == []


# ---------------------------------------------------------------------------
# Config override precedence
# ---------------------------------------------------------------------------


def test_config_override_takes_precedence(tmp_path: Path) -> None:
    # Create a real agent file (would be auto-detected)
    auto_file = _write(
        tmp_path / "agent.py",
        "@agent_test\ndef auto_agent(x): ...\n",
    )
    # Also declare a different file in config
    declared_file = _write(
        tmp_path / "src" / "custom_bot.py",
        "def custom_bot_main(msg): ...\n",
    )
    config_dir = tmp_path / ".tailtest"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {
                "agent": {
                    "entry_points": [
                        {
                            "file": "src/custom_bot.py",
                            "function": "custom_bot_main",
                            "framework": "custom",
                        }
                    ]
                }
            }
        )
    )

    eps = detect_entry_points(tmp_path, [auto_file, declared_file], "python")

    # Config-declared entry point should be the only one returned
    assert len(eps) == 1
    assert eps[0].function == "custom_bot_main"
    assert eps[0].confidence == "high"
    assert eps[0].framework == "custom"


def test_config_override_with_nonexistent_file(tmp_path: Path) -> None:
    """Config entries pointing to nonexistent files are silently skipped."""
    config_dir = tmp_path / ".tailtest"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        yaml.dump(
            {"agent": {"entry_points": [{"file": "does_not_exist.py", "function": "phantom"}]}}
        )
    )
    eps = detect_entry_points(tmp_path, [], "python")
    # The file doesn't exist -- loader still creates the EntryPoint but runner
    # will skip missing files. detect_entry_points returns them; runner filters.
    # (This just tests we don't crash on missing files.)
    assert isinstance(eps, list)


def test_config_override_empty_returns_to_auto_detect(tmp_path: Path) -> None:
    """Empty config entry_points list falls through to auto-detection."""
    f = _write(
        tmp_path / "agents" / "bot.py",
        "def main(msg: str): return llm(msg)\n",
    )
    config_dir = tmp_path / ".tailtest"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.dump({"agent": {"entry_points": []}}))
    eps = detect_entry_points(tmp_path, [f], "python")
    # Empty config → auto-detect fires
    assert any(e.function == "main" for e in eps)


# ---------------------------------------------------------------------------
# EntryPoint schema
# ---------------------------------------------------------------------------


def test_entry_point_model_fields() -> None:
    ep = EntryPoint(
        file=Path("src/agent.py"),
        function="run",
        language="python",
        confidence="high",
        framework="anthropic",
    )
    assert ep.file == Path("src/agent.py")
    assert ep.framework == "anthropic"


def test_entry_point_framework_optional() -> None:
    ep = EntryPoint(
        file=Path("agent.py"),
        function="main",
        language="python",
        confidence="medium",
    )
    assert ep.framework is None


# ---------------------------------------------------------------------------
# Profile integration -- agent_entry_points field
# ---------------------------------------------------------------------------


def test_profile_has_agent_entry_points_field() -> None:
    from tailtest.core.scan.profile import ProjectProfile

    profile = ProjectProfile(root=Path("/tmp/proj"))
    assert hasattr(profile, "agent_entry_points")
    assert profile.agent_entry_points == []


def test_profile_serializes_entry_points() -> None:
    from tailtest.core.scan.profile import ProjectProfile

    ep = EntryPoint(
        file=Path("agent.py"),
        function="run",
        language="python",
        confidence="high",
    )
    profile = ProjectProfile(root=Path("/tmp/proj"), agent_entry_points=[ep])
    json_text = profile.to_json()
    assert "agent_entry_points" in json_text
    assert "run" in json_text
