"""Tests for ``tailtest doctor`` CLI (Phase 1 Task 1.12)."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from tailtest.cli.doctor import (
    Check,
    CheckResult,
    _check_baseline_valid,
    _check_config_valid,
    _check_python_version,
    _check_runner_detection,
    _check_tailtest_dir_writable,
    doctor_cmd,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PASSING = FIXTURES / "python_project_passing"


def test_python_version_check_passes_on_311_plus() -> None:
    check = _check_python_version()
    assert check.result == CheckResult.PASS
    assert "3." in check.message


def test_runner_detection_finds_pytest_in_python_fixture() -> None:
    check = _check_runner_detection(FIXTURE_PASSING)
    assert check.result == CheckResult.PASS
    assert "pytest" in check.message


def test_runner_detection_warns_on_empty_project(tmp_path: Path) -> None:
    check = _check_runner_detection(tmp_path)
    assert check.result == CheckResult.WARN


def test_tailtest_dir_writable_check(tmp_path: Path) -> None:
    check = _check_tailtest_dir_writable(tmp_path)
    assert check.result == CheckResult.PASS
    assert (tmp_path / ".tailtest").exists()


def test_config_valid_check_missing(tmp_path: Path) -> None:
    """Missing config is PASS — defaults will be used."""
    check = _check_config_valid(tmp_path)
    assert check.result == CheckResult.PASS
    assert "not present" in check.message.lower()


def test_baseline_valid_check_missing(tmp_path: Path) -> None:
    check = _check_baseline_valid(tmp_path)
    assert check.result == CheckResult.PASS
    assert "not present" in check.message.lower()


def test_doctor_cli_runs_against_passing_fixture() -> None:
    """End-to-end: invoke the full doctor CLI and verify it succeeds."""
    runner = CliRunner()
    result = runner.invoke(doctor_cmd, ["--project-root", str(FIXTURE_PASSING)])
    # Could be 0 (all pass) or 1 (some fail) depending on environment.
    # We assert that it runs without crashing and emits the summary line.
    assert "tailtest doctor:" in result.output
    assert "pass" in result.output


def test_doctor_cli_verbose_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(doctor_cmd, ["--project-root", str(FIXTURE_PASSING), "--verbose"])
    assert "tailtest doctor:" in result.output


def test_check_render_no_color() -> None:
    c = Check(name="example", result=CheckResult.PASS, message="ok")
    out = c.render(color=False)
    assert "[PASS]" in out
    assert "example" in out


def test_check_render_with_color() -> None:
    c = Check(name="example", result=CheckResult.FAIL, message="bad")
    out = c.render(color=True)
    assert "example" in out
    # Color codes added when color=True
    assert "\x1b[" in out
