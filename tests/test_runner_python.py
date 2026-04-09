"""Tests for PythonRunner (Phase 1 Task 1.2a).

Uses two fixture Python projects under tests/fixtures/:
- python_project_passing: 3 tests, all passing
- python_project_failing: 3 tests, 1 passing, 1 failing, 1 skipped
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.core.runner import RunnerNotAvailable
from tailtest.core.runner.python import PythonRunner

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSING_FIXTURE = FIXTURES / "python_project_passing"
FAILING_FIXTURE = FIXTURES / "python_project_failing"


# --- Discovery -----------------------------------------------------------


def test_discover_passing_fixture() -> None:
    runner = PythonRunner(PASSING_FIXTURE)
    assert runner.discover() is True


def test_discover_failing_fixture() -> None:
    runner = PythonRunner(FAILING_FIXTURE)
    assert runner.discover() is True


def test_discover_empty_project(tmp_path: Path) -> None:
    """A project without pytest config or tests/ dir should not discover."""
    runner = PythonRunner(tmp_path)
    assert runner.discover() is False


def test_discover_raises_when_pytest_missing(monkeypatch, tmp_path: Path) -> None:
    """If pytest is on PATH but missing, discover raises RunnerNotAvailable."""
    # Build a minimal pytest project
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_dummy.py").write_text("def test_x(): pass\n")

    import shutil

    monkeypatch.setattr(shutil, "which", lambda _name: None)

    runner = PythonRunner(tmp_path)
    with pytest.raises(RunnerNotAvailable):
        runner.discover()


# --- Execution: passing suite -------------------------------------------


@pytest.mark.asyncio
async def test_run_passing_suite() -> None:
    """All 3 tests in the passing fixture should pass — zero findings."""
    runner = PythonRunner(PASSING_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="run-passing", timeout_seconds=60.0)
    assert batch.tests_passed == 3
    assert batch.tests_failed == 0
    assert batch.tests_skipped == 0
    assert batch.findings == []
    assert "3/3 tests passed" in batch.summary_line


# --- Execution: failing suite -------------------------------------------


@pytest.mark.asyncio
async def test_run_failing_suite() -> None:
    """The failing fixture has 1 passing, 1 failing, 1 skipped test."""
    runner = PythonRunner(FAILING_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="run-failing", timeout_seconds=60.0)

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert batch.tests_skipped == 1
    assert len(batch.findings) == 1

    finding = batch.findings[0]
    assert finding.kind == FindingKind.TEST_FAILURE
    assert finding.severity == Severity.HIGH
    assert finding.rule_id is not None
    assert "test_subtract_real_bug" in finding.rule_id
    # Pytest's JUnit XML records the test file path
    assert "test_buggy.py" in str(finding.file)


@pytest.mark.asyncio
async def test_run_failing_suite_summary_includes_counts() -> None:
    runner = PythonRunner(FAILING_FIXTURE)
    batch = await runner.run([], run_id="r", timeout_seconds=60.0)
    assert "1 failed" in batch.summary_line
    assert "1 skipped" in batch.summary_line


# --- Execution: explicit test filter ------------------------------------


@pytest.mark.asyncio
async def test_run_with_explicit_test_id() -> None:
    """Passing an explicit test ID runs only that test."""
    runner = PythonRunner(PASSING_FIXTURE)
    test_id = "tests/test_math.py::test_add_simple"
    batch = await runner.run([test_id], run_id="r", timeout_seconds=60.0)
    assert batch.tests_passed == 1
    assert batch.tests_failed == 0


# --- Impacted-test heuristic --------------------------------------------


def test_impacted_heuristic_matches_by_stem(tmp_path: Path) -> None:
    """The fallback heuristic should match test files that mention the changed stem."""
    # Build a tiny project with a test that references a module stem
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "widget.py").write_text("def make_widget(): return 42\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_widget.py").write_text("from widget import make_widget\n")
    (tests_dir / "test_other.py").write_text("def test_zzz(): assert True\n")

    runner = PythonRunner(tmp_path)
    impacted = runner._impacted_via_heuristic([Path("src/widget.py")])
    assert len(impacted) == 1
    assert "test_widget.py" in impacted[0]


def test_impacted_heuristic_empty_when_no_match(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_unrelated.py").write_text("def test_z(): pass\n")

    runner = PythonRunner(tmp_path)
    impacted = runner._impacted_via_heuristic([Path("src/widget.py")])
    assert impacted == []


# --- JUnit XML parsing ---------------------------------------------------


def test_parse_junit_wraps_bare_testsuite() -> None:
    """pytest JUnit output is sometimes <testsuites>, sometimes bare <testsuite>."""
    bare = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="1" failures="0" errors="0" skipped="0" time="0.01">
  <testcase classname="tests.test_foo" name="test_bar" file="tests/test_foo.py" time="0.001" />
</testsuite>
"""
    runner = PythonRunner(Path("."))
    batch = runner._parse_junit(junit_xml=bare, run_id="r", duration_ms=10.0)
    assert batch.tests_passed == 1
    assert batch.tests_failed == 0
    assert batch.findings == []


def test_parse_junit_captures_failure_message() -> None:
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0" time="0.02">
  <testcase classname="tests.test_foo" name="test_good" file="tests/test_foo.py" time="0.001" />
  <testcase classname="tests.test_foo" name="test_bad" file="tests/test_foo.py" line="15" time="0.003">
    <failure message="assert 1 == 2">AssertionError: assert 1 == 2</failure>
  </testcase>
</testsuite>
</testsuites>
"""
    runner = PythonRunner(Path("."))
    batch = runner._parse_junit(junit_xml=xml, run_id="r", duration_ms=20.0)
    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    f = batch.findings[0]
    assert "assert 1 == 2" in f.message
    assert f.claude_hint == "assert 1 == 2"


def test_parse_junit_handles_errors_as_failures() -> None:
    """A pytest <error> (not <failure>) should also be captured."""
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" tests="1" failures="0" errors="1" skipped="0" time="0.01">
  <testcase classname="tests.test_foo" name="test_err" file="tests/test_foo.py" time="0.001">
    <error message="ZeroDivisionError">division by zero</error>
  </testcase>
</testsuite>
</testsuites>
"""
    runner = PythonRunner(Path("."))
    batch = runner._parse_junit(junit_xml=xml, run_id="r", duration_ms=10.0)
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1


def test_parse_junit_preserves_collection_error_text() -> None:
    """Collection errors must surface the underlying ImportError text.

    Regression test for the Checkpoint E CoreCoder dogfood: pytest sets
    ``message="collection failure"`` and puts the actual ``ModuleNotFoundError``
    into the body. The earlier ``message_attr or text_content`` collapsed
    the body, so the user only saw "collection failure" with no hint of
    the missing module. The fix combines both when present.
    """
    xml = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
<testsuite name="pytest" tests="1" failures="0" errors="1" skipped="0" time="0.01">
  <testcase classname="tests.test_agent" name="test_agent" file="tests/test_agent.py" time="0">
    <error message="collection failure">ModuleNotFoundError: No module named 'openai'</error>
  </testcase>
</testsuite>
</testsuites>
"""
    runner = PythonRunner(Path("."))
    batch = runner._parse_junit(junit_xml=xml, run_id="r", duration_ms=10.0)
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    finding = batch.findings[0]
    # Both the generic banner AND the underlying error must be preserved.
    assert "collection failure" in finding.message
    assert "ModuleNotFoundError" in finding.message
    assert "openai" in finding.message


# --- Pytest path resolution (venv-aware) ---------------------------------


def test_resolve_pytest_prefers_project_venv(tmp_path: Path) -> None:
    """A target project's .venv/bin/pytest should win over PATH.

    Regression test for the Checkpoint E CoreCoder dogfood: tailtest used
    PATH-resolved pytest (its own venv) and every test in the target
    collection-failed on missing target deps.
    """
    venv_pytest = tmp_path / ".venv" / "bin" / "pytest"
    venv_pytest.parent.mkdir(parents=True)
    venv_pytest.write_text("#!/bin/sh\nexec /usr/bin/env true\n")
    venv_pytest.chmod(0o755)

    runner = PythonRunner(tmp_path)
    resolved = runner._resolve_pytest_path()
    assert resolved == str(venv_pytest)


def test_resolve_pytest_falls_back_to_alt_venv(tmp_path: Path) -> None:
    """``venv/`` (no leading dot) is also accepted."""
    venv_pytest = tmp_path / "venv" / "bin" / "pytest"
    venv_pytest.parent.mkdir(parents=True)
    venv_pytest.write_text("#!/bin/sh\nexec /usr/bin/env true\n")
    venv_pytest.chmod(0o755)

    runner = PythonRunner(tmp_path)
    resolved = runner._resolve_pytest_path()
    assert resolved == str(venv_pytest)


def test_resolve_pytest_falls_back_to_path(tmp_path: Path, monkeypatch) -> None:
    """When the target has no venv, fall back to PATH."""
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/pytest")
    runner = PythonRunner(tmp_path)
    resolved = runner._resolve_pytest_path()
    assert resolved == "/usr/local/bin/pytest"


def test_resolve_pytest_returns_none_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = PythonRunner(tmp_path)
    assert runner._resolve_pytest_path() is None
