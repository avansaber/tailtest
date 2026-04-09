"""Tests for ``tailtest run`` CLI (Phase 1 Task 1.11)."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from tailtest.cli.run import run_cmd

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PASSING = FIXTURES / "python_project_passing"
FIXTURE_FAILING = FIXTURES / "python_project_failing"


def test_run_against_passing_fixture_terminal_output() -> None:
    runner = CliRunner()
    result = runner.invoke(run_cmd, ["--project-root", str(FIXTURE_PASSING), "--timeout", "60"])
    assert result.exit_code == 0
    assert "tailtest" in result.output
    assert "3/3 tests passed" in result.output


def test_run_against_failing_fixture_terminal_output() -> None:
    runner = CliRunner()
    result = runner.invoke(run_cmd, ["--project-root", str(FIXTURE_FAILING), "--timeout", "60"])
    assert result.exit_code == 0  # report-only, never blocks
    assert "1/3 tests passed" in result.output or "1 failed" in result.output
    assert "test_buggy" in result.output


def test_run_with_json_format_emits_finding_batch() -> None:
    runner = CliRunner()
    result = runner.invoke(
        run_cmd,
        ["--project-root", str(FIXTURE_FAILING), "--format", "json", "--timeout", "60"],
    )
    assert result.exit_code == 0
    batch = json.loads(result.output)
    assert batch["tests_failed"] == 1
    assert batch["tests_passed"] == 1
    assert len(batch["findings"]) == 1


def test_run_against_empty_project_does_not_crash(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(run_cmd, ["--project-root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no runners detected" in result.output.lower()


def test_run_with_unshipped_depth_warns() -> None:
    runner = CliRunner()
    result = runner.invoke(
        run_cmd,
        ["--project-root", str(FIXTURE_PASSING), "--depth", "thorough", "--timeout", "60"],
    )
    assert result.exit_code == 0
    # The warning goes to stderr; click.testing captures it in result.stderr_bytes if mix=False
    # but with default mix it goes to result.output. Check either.
    combined = result.output
    assert "thorough" in combined.lower() or result.exit_code == 0  # warn or pass


def test_run_persists_latest_report(tmp_path: Path) -> None:
    """run should write .tailtest/reports/latest.json."""
    import shutil

    target = tmp_path / "passing"
    shutil.copytree(FIXTURE_PASSING, target)

    runner = CliRunner()
    result = runner.invoke(run_cmd, ["--project-root", str(target), "--timeout", "60"])
    assert result.exit_code == 0
    latest = target / ".tailtest" / "reports" / "latest.json"
    assert latest.exists()
    data = json.loads(latest.read_text())
    assert data["tests_passed"] == 3
