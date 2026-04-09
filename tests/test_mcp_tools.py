"""Tests for the 6 MCP tools (Phase 1 Checkpoint E.2, Task 1.4)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tailtest.mcp.tools import (
    ALL_TOOLS,
    GenerateTestsTool,
    GetBaselineTool,
    ImpactedTestsTool,
    RunTestsTool,
    ScanProjectTool,
    TailtestStatusTool,
)
from tailtest.mcp.tools.base import BaseTool

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PASSING = FIXTURES / "python_project_passing"
FIXTURE_FAILING = FIXTURES / "python_project_failing"
FIXTURE_SCANNER_AI = FIXTURES / "scanner_python_ai"
FIXTURE_SCANNER_PLAIN = FIXTURES / "scanner_plain"


# --- Registration + contract tests --------------------------------------


def test_all_tools_has_six_entries() -> None:
    assert len(ALL_TOOLS) == 6
    names = {cls.name for cls in ALL_TOOLS}
    assert names == {
        "scan_project",
        "impacted_tests",
        "run_tests",
        "generate_tests",
        "get_baseline",
        "tailtest_status",
    }


def test_every_tool_has_required_class_attrs() -> None:
    for cls in ALL_TOOLS:
        assert issubclass(cls, BaseTool)
        assert cls.name, f"{cls.__name__} missing name"
        assert cls.description, f"{cls.__name__} missing description"
        assert cls.input_schema.get("type") == "object"


def test_every_tool_definition_is_valid_mcp_schema() -> None:
    for cls in ALL_TOOLS:
        d = cls.definition()
        assert "name" in d
        assert "description" in d
        assert "inputSchema" in d
        assert d["inputSchema"]["type"] == "object"


def test_base_tool_definition_requires_name() -> None:
    class UnnamedTool(BaseTool):
        async def invoke(self, arguments: dict) -> dict:  # type: ignore[override]
            return {"content": [], "isError": False}

    with pytest.raises(ValueError, match="no `name`"):
        UnnamedTool.definition()


# --- scan_project -------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_project_tool_on_ai_fixture() -> None:
    tool = ScanProjectTool(FIXTURE_SCANNER_AI)
    response = await tool.invoke({})

    assert response["isError"] is False
    text = response["content"][0]["text"]
    profile = json.loads(text)
    assert profile["primary_language"] == "python"
    assert profile["ai_surface"] == "agent"
    assert profile["likely_vibe_coded"] is True


@pytest.mark.asyncio
async def test_scan_project_tool_on_plain_fixture() -> None:
    tool = ScanProjectTool(FIXTURE_SCANNER_PLAIN)
    response = await tool.invoke({})

    assert response["isError"] is False
    profile = json.loads(response["content"][0]["text"])
    assert profile["primary_language"] == "python"
    assert profile["ai_surface"] == "none"
    assert profile["likely_vibe_coded"] is False


@pytest.mark.asyncio
async def test_scan_project_tool_deep_flag_is_accepted() -> None:
    """Phase 1 treats `deep: true` as shallow. Should not error."""
    tool = ScanProjectTool(FIXTURE_SCANNER_PLAIN)
    response = await tool.invoke({"deep": True})
    assert response["isError"] is False


@pytest.mark.asyncio
async def test_scan_project_tool_handles_missing_project(tmp_path: Path) -> None:
    """Scanner on an empty directory should succeed, not error."""
    tool = ScanProjectTool(tmp_path)
    response = await tool.invoke({})
    assert response["isError"] is False
    profile = json.loads(response["content"][0]["text"])
    assert profile["total_files_walked"] == 0


# --- impacted_tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_impacted_tests_requires_files_list() -> None:
    tool = ImpactedTestsTool(FIXTURE_PASSING)
    response = await tool.invoke({"files": "not a list"})
    assert response["isError"] is True


@pytest.mark.asyncio
async def test_impacted_tests_empty_project_returns_empty(tmp_path: Path) -> None:
    tool = ImpactedTestsTool(tmp_path)
    response = await tool.invoke({"files": ["src/foo.py"]})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["test_ids"] == []
    assert "no runners detected" in payload["reason"]


@pytest.mark.asyncio
async def test_impacted_tests_returns_test_ids_for_python_fixture() -> None:
    tool = ImpactedTestsTool(FIXTURE_PASSING)
    response = await tool.invoke({"files": ["src/fixture_passing/__init__.py"]})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["runner"] == "pytest"
    assert isinstance(payload["test_ids"], list)
    # May be empty if pytest-testmon isn't installed; fallback heuristic should
    # find test_math.py because it mentions fixture_passing.
    # Not strictly asserted so the test works regardless of testmon availability.


# --- run_tests ----------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tests_on_passing_fixture() -> None:
    tool = RunTestsTool(FIXTURE_PASSING)
    response = await tool.invoke({"test_ids": [], "timeout": 60})

    assert response["isError"] is False
    batch = json.loads(response["content"][0]["text"])
    assert batch["tests_passed"] == 3
    assert batch["tests_failed"] == 0
    assert batch["findings"] == []


@pytest.mark.asyncio
async def test_run_tests_on_failing_fixture_emits_finding() -> None:
    tool = RunTestsTool(FIXTURE_FAILING)
    response = await tool.invoke({"test_ids": [], "timeout": 60})

    assert response["isError"] is False
    batch = json.loads(response["content"][0]["text"])
    assert batch["tests_passed"] == 1
    assert batch["tests_failed"] == 1
    assert batch["tests_skipped"] == 1
    assert len(batch["findings"]) == 1
    assert batch["findings"][0]["kind"] == "test_failure"


@pytest.mark.asyncio
async def test_run_tests_writes_latest_json(tmp_path: Path, monkeypatch) -> None:
    """run_tests should persist the full batch to .tailtest/reports/latest.json."""
    # Copy the failing fixture into tmp_path so we can observe side effects.
    import shutil

    target = tmp_path / "failing"
    shutil.copytree(FIXTURE_FAILING, target)

    tool = RunTestsTool(target)
    await tool.invoke({"test_ids": [], "timeout": 60})

    latest = target / ".tailtest" / "reports" / "latest.json"
    assert latest.exists()
    data = json.loads(latest.read_text())
    assert data["tests_failed"] == 1


@pytest.mark.asyncio
async def test_run_tests_empty_project_returns_empty_batch(tmp_path: Path) -> None:
    tool = RunTestsTool(tmp_path)
    response = await tool.invoke({"test_ids": []})
    assert response["isError"] is False
    batch = json.loads(response["content"][0]["text"])
    assert batch["tests_passed"] == 0
    assert batch["tests_failed"] == 0


# --- generate_tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_generate_tests_rejects_missing_file_arg() -> None:
    """Missing 'file' argument is surfaced as an error response."""
    tool = GenerateTestsTool(FIXTURE_PASSING)
    response = await tool.invoke({})
    assert response["isError"] is True


@pytest.mark.asyncio
async def test_generate_tests_skips_unsupported_language(tmp_path: Path) -> None:
    """An unsupported source language is surfaced as a skipped status, not an error."""
    (tmp_path / "example.rs").write_text("fn main() {}\n")
    tool = GenerateTestsTool(tmp_path)
    response = await tool.invoke({"file": "example.rs", "project_root": str(tmp_path)})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["status"] == "skipped"
    assert "unsupported language" in payload["reason"]


@pytest.mark.asyncio
async def test_generate_tests_skips_missing_source(tmp_path: Path) -> None:
    """A nonexistent source file surfaces as a skipped status."""
    tool = GenerateTestsTool(tmp_path)
    response = await tool.invoke({"file": "nowhere.py", "project_root": str(tmp_path)})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["status"] == "skipped"
    assert "does not exist" in payload["reason"]


# --- get_baseline -------------------------------------------------------


@pytest.mark.asyncio
async def test_get_baseline_empty(tmp_path: Path) -> None:
    tool = GetBaselineTool(tmp_path)
    response = await tool.invoke({})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["exists"] is False
    assert payload["total_entries"] == 0
    assert payload["recent"] == []


@pytest.mark.asyncio
async def test_get_baseline_with_entries(tmp_path: Path) -> None:
    """Populate a baseline then read it back via the tool."""
    from tailtest.core.baseline import BaselineEntry, BaselineFile, BaselineManager
    from tailtest.core.findings.schema import Finding, FindingKind, Severity

    tailtest_dir = tmp_path / ".tailtest"
    manager = BaselineManager(tailtest_dir)

    f1 = Finding.create(
        kind=FindingKind.SAST,
        severity=Severity.MEDIUM,
        file="src/foo.py",
        line=10,
        message="eval user input",
        run_id="r",
        rule_id="semgrep.eval",
    )
    entry = BaselineEntry.from_finding(f1)
    manager.save(BaselineFile(entries={entry.id: entry}))

    tool = GetBaselineTool(tmp_path)
    response = await tool.invoke({})
    payload = json.loads(response["content"][0]["text"])
    assert payload["exists"] is True
    assert payload["total_entries"] == 1
    assert payload["counts_by_kind"]["sast"] == 1
    assert len(payload["recent"]) == 1
    assert payload["recent"][0]["id"] == entry.id


# --- tailtest_status ----------------------------------------------------


@pytest.mark.asyncio
async def test_tailtest_status_on_empty_project(tmp_path: Path) -> None:
    tool = TailtestStatusTool(tmp_path)
    response = await tool.invoke({})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert "tailtest_version" in payload
    assert payload["config"]["depth"] == "standard"
    assert payload["baseline"]["total_entries"] == 0
    assert payload["last_run"] is None


@pytest.mark.asyncio
async def test_tailtest_status_on_real_fixture() -> None:
    tool = TailtestStatusTool(FIXTURE_SCANNER_AI)
    response = await tool.invoke({})
    assert response["isError"] is False
    payload = json.loads(response["content"][0]["text"])
    assert payload["profile_summary"]["primary_language"] == "python"
    assert payload["profile_summary"]["ai_surface"] == "agent"
    assert payload["profile_summary"]["likely_vibe_coded"] is True
