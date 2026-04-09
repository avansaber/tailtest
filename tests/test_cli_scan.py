"""Tests for ``tailtest scan`` CLI."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from tailtest.cli.scan import scan_cmd

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PYTHON_AI = FIXTURES / "scanner_python_ai"
FIXTURE_PLAIN = FIXTURES / "scanner_plain"


def test_scan_summary_default_output() -> None:
    runner = CliRunner()
    result = runner.invoke(scan_cmd, ["--project-root", str(FIXTURE_PYTHON_AI)])
    assert result.exit_code == 0
    assert "python" in result.output
    assert "vibe-coded" in result.output  # because CLAUDE.md + AGENTS.md
    assert "frameworks:" in result.output


def test_scan_show_emits_full_json() -> None:
    runner = CliRunner()
    result = runner.invoke(scan_cmd, ["--project-root", str(FIXTURE_PYTHON_AI), "--show"])
    assert result.exit_code == 0
    # Output should be parseable JSON
    profile = json.loads(result.output)
    assert profile["primary_language"] == "python"
    assert profile["ai_surface"] == "agent"


def test_scan_save_writes_profile_json(tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "fixture"
    shutil.copytree(FIXTURE_PLAIN, target)

    runner = CliRunner()
    result = runner.invoke(scan_cmd, ["--project-root", str(target), "--save"])
    assert result.exit_code == 0
    profile_path = target / ".tailtest" / "profile.json"
    assert profile_path.exists()
    data = json.loads(profile_path.read_text())
    assert data["primary_language"] == "python"


def test_scan_no_save_does_not_persist(tmp_path: Path) -> None:
    import shutil

    target = tmp_path / "fixture"
    shutil.copytree(FIXTURE_PLAIN, target)

    runner = CliRunner()
    result = runner.invoke(scan_cmd, ["--project-root", str(target)])
    assert result.exit_code == 0
    # No --save → no file written
    assert not (target / ".tailtest" / "profile.json").exists()


def test_scan_deep_falls_back_to_shallow_in_phase_1() -> None:
    """--deep is accepted but Phase 1 silently uses shallow."""
    runner = CliRunner()
    result = runner.invoke(scan_cmd, ["--project-root", str(FIXTURE_PLAIN), "--deep"])
    assert result.exit_code == 0
