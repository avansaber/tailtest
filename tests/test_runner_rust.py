"""Tests for RustRunner and RustTIA (Phase 4.5 Tasks 4.5.1 + 4.5.2).

Uses three fixture Rust projects under tests/fixtures/:
- rust_project_passing: 2 tests, all passing
- rust_project_failing: 2 tests, 1 passing, 1 failing
- rust_workspace: workspace with crate_a (1 test) and crate_b (1 test)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.core.runner.rust import RustRunner, _parse_cargo_output
from tailtest.core.tia.rust import RustTIA

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSING_FIXTURE = FIXTURES / "rust_project_passing"
FAILING_FIXTURE = FIXTURES / "rust_project_failing"
WORKSPACE_FIXTURE = FIXTURES / "rust_workspace"

# A Python project to verify Rust runner does NOT discover it.
PYTHON_FIXTURE = FIXTURES / "python_project_passing"


# --- Discovery -----------------------------------------------------------


def test_rust_runner_discovers_cargo_project() -> None:
    runner = RustRunner(PASSING_FIXTURE)
    assert runner.discover() is True


def test_rust_runner_does_not_discover_python_project() -> None:
    runner = RustRunner(PYTHON_FIXTURE)
    assert runner.discover() is False


def test_rust_runner_does_not_discover_empty_project(tmp_path: Path) -> None:
    runner = RustRunner(tmp_path)
    assert runner.discover() is False


# --- Execution: passing suite -------------------------------------------


@pytest.mark.asyncio
async def test_rust_runner_run_passing() -> None:
    """All 2 tests in the passing fixture should pass -- zero findings."""
    runner = RustRunner(PASSING_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="run-rust-pass", timeout_seconds=120.0)
    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []


# --- Execution: failing suite -------------------------------------------


@pytest.mark.asyncio
async def test_rust_runner_run_failing() -> None:
    """The failing fixture has 1 passing and 1 failing test."""
    runner = RustRunner(FAILING_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="run-rust-fail", timeout_seconds=120.0)

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1

    finding = batch.findings[0]
    assert finding.kind == FindingKind.TEST_FAILURE
    assert finding.severity == Severity.HIGH
    assert "test_subtract" in finding.message


# --- Execution: workspace -----------------------------------------------


@pytest.mark.asyncio
async def test_rust_runner_run_workspace() -> None:
    """Workspace runner should run all crates and collect results."""
    runner = RustRunner(WORKSPACE_FIXTURE)
    assert runner.discover() is True
    batch = await runner.run([], run_id="run-rust-ws", timeout_seconds=120.0)
    # crate_a has 1 test, crate_b has 1 test -- both pass
    assert batch.tests_passed >= 2
    assert batch.tests_failed == 0
    assert batch.findings == []


# --- TIA: RustTIA -------------------------------------------------------


def test_rust_tia_finds_affected_crate() -> None:
    """Editing a file in crate_a should return only crate_a."""
    tia = RustTIA(WORKSPACE_FIXTURE)
    changed = [WORKSPACE_FIXTURE / "crate_a" / "src" / "lib.rs"]
    crates = tia.impacted_crates(changed)
    assert crates == ["crate_a"]


def test_rust_tia_finds_crate_b() -> None:
    """Editing a file in crate_b should return only crate_b."""
    tia = RustTIA(WORKSPACE_FIXTURE)
    changed = [WORKSPACE_FIXTURE / "crate_b" / "src" / "lib.rs"]
    crates = tia.impacted_crates(changed)
    assert crates == ["crate_b"]


def test_rust_tia_returns_empty_for_no_files() -> None:
    tia = RustTIA(WORKSPACE_FIXTURE)
    assert tia.impacted_crates([]) == []


def test_rust_tia_returns_empty_for_unmapped_file(tmp_path: Path) -> None:
    """A file with no Cargo.toml ancestor should trigger run-all (empty list)."""
    tia = RustTIA(tmp_path)
    orphan = tmp_path / "src" / "lib.rs"
    orphan.parent.mkdir()
    orphan.write_text("// no Cargo.toml here")
    result = tia.impacted_crates([orphan])
    assert result == []


# --- RustRunner.impacted ------------------------------------------------


@pytest.mark.asyncio
async def test_rust_runner_impacted_maps_to_crate_name() -> None:
    """impacted([lib.rs]) in a single-crate project returns the crate name."""
    runner = RustRunner(PASSING_FIXTURE)
    changed = [PASSING_FIXTURE / "src" / "lib.rs"]
    crates = await runner.impacted(changed)
    assert crates == ["fixture_pass"]


@pytest.mark.asyncio
async def test_rust_runner_impacted_empty_returns_empty() -> None:
    runner = RustRunner(PASSING_FIXTURE)
    assert await runner.impacted([]) == []


# --- _parse_cargo_output: unit tests ------------------------------------

_PASSING_OUTPUT = """\
running 2 tests
test tests::test_add ... ok
test tests::test_add_zero ... ok

test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

   Doc-tests fixture_pass

running 0 tests

test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""

_FAILING_OUTPUT = """\
running 2 tests
test tests::test_subtract_zero ... ok
test tests::test_subtract ... FAILED

failures:

---- tests::test_subtract stdout ----

thread 'tests::test_subtract' panicked at src/lib.rs:11:9:
assertion `left == right` failed
  left: 8
 right: 2
note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace


failures:
    tests::test_subtract

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s
"""


def test_parse_cargo_output_passing() -> None:
    batch = _parse_cargo_output(_PASSING_OUTPUT, run_id="r", package="fixture_pass")
    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []


def test_parse_cargo_output_failing() -> None:
    batch = _parse_cargo_output(_FAILING_OUTPUT, run_id="r", package="fixture_fail")
    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    finding = batch.findings[0]
    assert finding.kind == FindingKind.TEST_FAILURE
    assert "test_subtract" in finding.message
    assert "src/lib.rs" in str(finding.file)
    assert finding.line == 11


def test_parse_cargo_output_extracts_panic_location() -> None:
    """Parser must extract file path and line number from the panic location."""
    batch = _parse_cargo_output(_FAILING_OUTPUT, run_id="r", package="")
    finding = batch.findings[0]
    assert finding.file == Path("src/lib.rs")
    assert finding.line == 11


def test_parse_cargo_output_rule_id_includes_test_name() -> None:
    batch = _parse_cargo_output(_FAILING_OUTPUT, run_id="r", package="fixture_fail")
    finding = batch.findings[0]
    assert finding.rule_id is not None
    assert "test_subtract" in finding.rule_id
