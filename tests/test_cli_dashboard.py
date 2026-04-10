"""Tests for ``tailtest dashboard`` CLI command."""

from __future__ import annotations

from click.testing import CliRunner

from tailtest.cli.dashboard import dashboard


def test_dashboard_command_exists() -> None:
    assert dashboard is not None


def test_dashboard_flag_no_open() -> None:
    runner = CliRunner()
    result = runner.invoke(dashboard, ["--help"])
    assert result.exit_code == 0
    assert "--no-open" in result.output


def test_dashboard_flag_port() -> None:
    runner = CliRunner()
    result = runner.invoke(dashboard, ["--help"])
    assert result.exit_code == 0
    assert "--port" in result.output


def test_dashboard_flag_dev() -> None:
    runner = CliRunner()
    result = runner.invoke(dashboard, ["--help"])
    assert result.exit_code == 0
    assert "--dev" in result.output
