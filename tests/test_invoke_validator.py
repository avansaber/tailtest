"""Tests for the invoke_validator MCP tool (Phase 5 Task 5.2).

All subprocess calls are mocked -- no real claude binary required.
Tests cover: normal findings, empty findings, timeout, crash, JSON parse
error, defensive layer (modification-pattern rejection), memory append,
and memory archive rotation.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.mcp.tools.invoke_validator import (
    InvokeValidatorTool,
    _append_memory,
    _build_initial_prompt,
    _load_memory_snippet,
    _maybe_archive_memory,
    _parse_validator_output,
    _strip_frontmatter,
    _to_findings,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_root(tmp_path: Path) -> Path:
    """A temp project root with a minimal agents/validator.md."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "validator.md").write_text(
        textwrap.dedent("""\
            ---
            name: tailtest-validator
            tools: Read Grep Glob Bash
            model: sonnet
            ---

            You are the validator. Return a JSON array of findings.
            """),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def tool(project_root: Path) -> InvokeValidatorTool:
    return InvokeValidatorTool(project_root)


def _fake_proc(stdout: str, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode("utf-8"), b"")
    )
    proc.kill = MagicMock()
    return proc


def _findings_json(*severities: str) -> str:
    findings = [
        {
            "severity": sev,
            "file": "src/foo.py",
            "line": 42,
            "message": f"Test finding ({sev})",
            "fix_suggestion": "Fix it",
            "reasoning": "Because reasons",
            "confidence": "high",
        }
        for sev in severities
    ]
    return json.dumps(findings)


# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_strip_frontmatter_removes_yaml() -> None:
    text = "---\nname: foo\n---\n\nBody here."
    assert _strip_frontmatter(text) == "Body here."


def test_strip_frontmatter_no_frontmatter() -> None:
    text = "Just body."
    assert _strip_frontmatter(text) == "Just body."


def test_load_memory_snippet_missing(tmp_path: Path) -> None:
    path = tmp_path / "validator.md"
    assert _load_memory_snippet(path) == ""


def test_load_memory_snippet_exists(tmp_path: Path) -> None:
    path = tmp_path / "validator.md"
    path.write_text("Some memory", encoding="utf-8")
    assert _load_memory_snippet(path) == "Some memory"


def test_load_memory_snippet_truncates_long(tmp_path: Path) -> None:
    path = tmp_path / "validator.md"
    path.write_text("x" * 5000, encoding="utf-8")
    snippet = _load_memory_snippet(path)
    assert len(snippet) <= 2000


def test_build_initial_prompt_includes_diff() -> None:
    prompt = _build_initial_prompt(
        file_paths=["src/foo.py"],
        diff="--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n+new line",
        context="",
        memory_snippet="",
    )
    assert "src/foo.py" in prompt
    assert "new line" in prompt


def test_build_initial_prompt_truncates_long_diff() -> None:
    big_diff = "+" + "x" * 10_000
    prompt = _build_initial_prompt(
        file_paths=[],
        diff=big_diff,
        context="",
        memory_snippet="",
    )
    assert "truncated" in prompt


def test_parse_validator_output_clean_json() -> None:
    raw = '[{"severity": "high", "file": "foo.py", "line": 1, "message": "bug"}]'
    findings, note = _parse_validator_output(raw)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert note == ""


def test_parse_validator_output_with_memory_note() -> None:
    raw = (
        '[{"severity": "low", "file": "foo.py", "line": 1, "message": "minor"}]'
        "\n<!-- validator-memory-append -->\n2026-04-10 found nothing serious"
    )
    findings, note = _parse_validator_output(raw)
    assert len(findings) == 1
    assert "2026-04-10" in note


def test_parse_validator_output_empty_array() -> None:
    findings, note = _parse_validator_output("[]")
    assert findings == []
    assert note == ""


def test_parse_validator_output_no_json() -> None:
    findings, note = _parse_validator_output("I found nothing to report.")
    assert findings == []


def test_parse_validator_output_invalid_json() -> None:
    findings, note = _parse_validator_output("[{broken json")
    assert findings == []


def test_to_findings_severity_mapping() -> None:
    data = [{"severity": "critical", "file": "x.py", "line": 1, "message": "oops"}]
    findings = _to_findings(data, run_id="r1")
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].kind == FindingKind.VALIDATOR


def test_to_findings_unknown_severity_defaults_medium() -> None:
    data = [{"severity": "cosmic", "file": "x.py", "line": 0, "message": "?"}]
    findings = _to_findings(data, run_id="r1")
    assert findings[0].severity == Severity.MEDIUM


def test_to_findings_attaches_reasoning_and_confidence() -> None:
    data = [
        {
            "severity": "high",
            "file": "x.py",
            "line": 5,
            "message": "bad",
            "reasoning": "chain of thought",
            "confidence": "low",
        }
    ]
    findings = _to_findings(data, run_id="r1")
    assert findings[0].reasoning == "chain of thought"
    assert findings[0].confidence == "low"


def test_to_findings_skips_non_dict_items() -> None:
    data: list[Any] = ["not a dict", {"severity": "low", "file": "x.py", "line": 0, "message": "ok"}]
    findings = _to_findings(data, run_id="r1")
    assert len(findings) == 1


def test_append_memory_creates_file(tmp_path: Path) -> None:
    path = tmp_path / "memory" / "validator.md"
    _append_memory(path, "First note")
    assert path.exists()
    content = path.read_text()
    assert "First note" in content


def test_maybe_archive_memory_triggers_at_limit(tmp_path: Path) -> None:
    path = tmp_path / "validator.md"
    # Write content above the 40,000-char threshold.
    entries = "\n---\n".join(["entry " + str(i) + " " + "x" * 1000 for i in range(45)])
    path.write_text(entries, encoding="utf-8")
    _maybe_archive_memory(path)
    archives = list(tmp_path.glob("validator-archive-*.md"))
    assert len(archives) == 1
    # Active file should be shorter.
    assert len(path.read_text()) < len(entries)


def test_maybe_archive_memory_no_op_when_small(tmp_path: Path) -> None:
    path = tmp_path / "validator.md"
    path.write_text("small content", encoding="utf-8")
    _maybe_archive_memory(path)
    assert not list(tmp_path.glob("validator-archive-*.md"))


# ---------------------------------------------------------------------------
# Full tool invocation tests (subprocess mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_validator_normal_findings(tool: InvokeValidatorTool) -> None:
    raw = _findings_json("high", "medium")
    proc = _fake_proc(raw)
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(raw.encode(), b"")):
            result = await tool.invoke(
                {"file_paths": ["src/foo.py"], "diff": "+new line\n", "timeout": 10}
            )
    assert not result["isError"]
    batch = json.loads(result["content"][0]["text"])
    assert batch["tests_failed"] > 0
    assert any(f["kind"] == "validator" for f in batch["findings"])


@pytest.mark.asyncio
async def test_invoke_validator_empty_findings(tool: InvokeValidatorTool) -> None:
    proc = _fake_proc("[]")
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(b"[]", b"")):
            result = await tool.invoke({"file_paths": [], "diff": "", "timeout": 10})
    assert not result["isError"]
    batch = json.loads(result["content"][0]["text"])
    assert batch["findings"] == []
    assert "nothing concerning" in batch["summary_line"]


@pytest.mark.asyncio
async def test_invoke_validator_timeout(tool: InvokeValidatorTool) -> None:
    import asyncio as aio

    proc = _fake_proc("")
    proc.communicate = AsyncMock(side_effect=aio.TimeoutError())
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        with patch("asyncio.wait_for", side_effect=aio.TimeoutError()):
            result = await tool.invoke({"timeout": 1})
    assert not result["isError"]
    batch = json.loads(result["content"][0]["text"])
    assert "timed out" in batch["summary_line"]


@pytest.mark.asyncio
async def test_invoke_validator_subagent_crash(tool: InvokeValidatorTool) -> None:
    with patch("asyncio.create_subprocess_exec", side_effect=OSError("spawn failed")):
        result = await tool.invoke({"timeout": 5})
    assert not result["isError"]
    batch = json.loads(result["content"][0]["text"])
    assert "error" in batch["summary_line"].lower() or "findings" in batch["summary_line"].lower()


@pytest.mark.asyncio
async def test_invoke_validator_claude_not_found(tool: InvokeValidatorTool) -> None:
    with patch("shutil.which", return_value=None):
        result = await tool.invoke({})
    assert result["isError"]
    assert "not found" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_invoke_validator_defensive_layer_blocks_diff_output(
    tool: InvokeValidatorTool,
) -> None:
    # Validator output that looks like a code-modification attempt.
    suspicious = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@ bad output"
    with patch("asyncio.create_subprocess_exec", return_value=_fake_proc(suspicious)):
        with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(suspicious.encode(), b"")):
            result = await tool.invoke({"diff": "+x\n", "timeout": 5})
    assert not result["isError"]
    batch = json.loads(result["content"][0]["text"])
    assert batch["findings"] == []


@pytest.mark.asyncio
async def test_invoke_validator_validator_md_not_found(tmp_path: Path) -> None:
    tool = InvokeValidatorTool(tmp_path)  # no agents/ dir
    result = await tool.invoke({})
    assert result["isError"]
    assert "not found" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_invoke_validator_appends_memory(
    tool: InvokeValidatorTool, project_root: Path
) -> None:
    note = "2026-04-10 Validated a refactor. No issues found."
    raw = f"[]\n<!-- validator-memory-append -->\n{note}"
    with patch("asyncio.create_subprocess_exec", return_value=_fake_proc(raw)):
        with patch("asyncio.wait_for", new_callable=AsyncMock, return_value=(raw.encode(), b"")):
            await tool.invoke({"timeout": 5})
    memory_path = project_root / ".tailtest" / "memory" / "validator.md"
    assert memory_path.exists()
    assert "2026-04-10" in memory_path.read_text()


def test_tool_is_registered_in_all_tools() -> None:
    from tailtest.mcp.tools import ALL_TOOLS
    names = [t.name for t in ALL_TOOLS]
    assert "invoke_validator" in names


def test_tool_definition_is_valid() -> None:
    defn = InvokeValidatorTool.definition()
    assert defn["name"] == "invoke_validator"
    assert "file_paths" in defn["inputSchema"]["properties"]
    assert "diff" in defn["inputSchema"]["properties"]
