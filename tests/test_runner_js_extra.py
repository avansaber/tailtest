"""Tests for NodeTestRunner, MochaRunner, AvaRunner, TapeRunner (Phase 4.5.9).

All tests use monkeypatched subprocess calls -- no real node/npm required.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.core.runner._tap import parse_tap
from tailtest.core.runner.ava import AvaRunner
from tailtest.core.runner.base import RunnerNotAvailable, ShellResult
from tailtest.core.runner.mocha import MochaRunner
from tailtest.core.runner.node_test import NodeTestRunner
from tailtest.core.runner.tape import TapeRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkg(tmp_path: Path, **fields: object) -> Path:
    """Write a minimal package.json to tmp_path and return the path."""
    data = {"name": "test-pkg", "version": "1.0.0", **fields}
    (tmp_path / "package.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def _shell(stdout: str = "", stderr: str = "", returncode: int = 0) -> ShellResult:
    return ShellResult(
        returncode=returncode, stdout=stdout, stderr=stderr, duration_ms=10.0
    )


# ---------------------------------------------------------------------------
# TAP parser
# ---------------------------------------------------------------------------


def test_tap_parse_passing() -> None:
    tap = "TAP version 13\nok 1 - adds numbers\nok 2 - handles zero\n"
    entries = parse_tap(tap)
    assert len(entries) == 2
    assert all(e.passed for e in entries)
    assert entries[0].name == "adds numbers"


def test_tap_parse_failure_with_yaml() -> None:
    tap = (
        "TAP version 13\n"
        "ok 1 - passes\n"
        "not ok 2 - fails\n"
        "  ---\n"
        "  message: 'Expected 1 to equal 2'\n"
        "  ...\n"
    )
    entries = parse_tap(tap)
    assert len(entries) == 2
    assert entries[1].passed is False
    assert "Expected 1 to equal 2" in entries[1].message


def test_tap_parse_skip_directive() -> None:
    tap = "ok 1 - skipped test # SKIP not applicable\n"
    entries = parse_tap(tap)
    assert entries[0].skipped is True
    assert entries[0].passed is True


def test_tap_parse_empty() -> None:
    assert parse_tap("") == []


def test_tap_parse_mixed() -> None:
    tap = (
        "ok 1 - passes\n"
        "not ok 2 - fails\n"
        "  ---\n"
        "  message: 'assertion failed'\n"
        "  ...\n"
        "ok 3 - also passes\n"
        "ok 4 - skip this # SKIP reason\n"
    )
    entries = parse_tap(tap)
    assert len(entries) == 4
    passed = [e for e in entries if e.passed and not e.skipped]
    failed = [e for e in entries if not e.passed]
    skipped = [e for e in entries if e.skipped]
    assert len(passed) == 2
    assert len(failed) == 1
    assert len(skipped) == 1
    assert failed[0].message == "assertion failed"


# ---------------------------------------------------------------------------
# _is_node_test_script helper
# ---------------------------------------------------------------------------


def test_is_node_test_script_direct() -> None:
    from tailtest.core.runner.node_test import _is_node_test_script
    assert _is_node_test_script("node --test") is True


def test_is_node_test_script_with_loader_flags() -> None:
    """Feynman-style: node --import tsx --test --test-concurrency=1 tests/*.test.ts"""
    from tailtest.core.runner.node_test import _is_node_test_script
    assert _is_node_test_script(
        "node --import tsx --test --test-concurrency=1 tests/*.test.ts"
    ) is True


def test_is_node_test_script_node_colon_test() -> None:
    from tailtest.core.runner.node_test import _is_node_test_script
    assert _is_node_test_script("tsx --import node:test tests/") is True


def test_is_node_test_script_jest_is_false() -> None:
    from tailtest.core.runner.node_test import _is_node_test_script
    assert _is_node_test_script("jest") is False


def test_is_node_test_script_npx_tsx_is_false() -> None:
    """npx tsx --test is tsx's own runner, not node:test."""
    from tailtest.core.runner.node_test import _is_node_test_script
    assert _is_node_test_script("npx tsx --test") is False


# ---------------------------------------------------------------------------
# NodeTestRunner -- discovery
# ---------------------------------------------------------------------------


def test_node_test_discover_false_no_package_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is False


def test_node_test_discover_false_no_node_binary(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "node --test"})
    monkeypatch.setattr(shutil, "which", lambda _: None)
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is False


def test_node_test_discover_false_no_node_test_script(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "jest"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is False


def test_node_test_discover_true_node_test_script(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "node --test tests/"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is True


def test_node_test_discover_defers_to_jsrunner_when_vitest(
    tmp_path: Path, monkeypatch
) -> None:
    """Projects with vitest in devDeps should NOT be picked up by NodeTestRunner."""
    _pkg(
        tmp_path,
        devDependencies={"vitest": "^2.0.0"},
        scripts={"test": "node --test tests/"},
    )
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is False


def test_node_test_discover_defers_to_jsrunner_when_jest(
    tmp_path: Path, monkeypatch
) -> None:
    _pkg(
        tmp_path,
        devDependencies={"jest": "^29.0.0"},
        scripts={"test": "node --test"},
    )
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is False


def test_node_test_discover_node_colon_test_script(tmp_path: Path, monkeypatch) -> None:
    """``node:test`` in scripts is also a valid signal."""
    _pkg(tmp_path, scripts={"test": "tsx --import node:test tests/"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is True


def test_node_test_discover_feynman_style_script(tmp_path: Path, monkeypatch) -> None:
    """Feynman uses ``node --import tsx --test --test-concurrency=1 tests/*.test.ts``."""
    _pkg(
        tmp_path,
        devDependencies={"tsx": "^4.21.0", "typescript": "^5.9.3"},
        scripts={"test": "node --import tsx --test --test-concurrency=1 tests/*.test.ts"},
    )
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = NodeTestRunner(tmp_path)
    assert runner.discover() is True


# ---------------------------------------------------------------------------
# NodeTestRunner -- JSON output parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_node_test_run_json_all_pass(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "node --test"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    ndjson = "\n".join([
        json.dumps({"type": "test:pass", "data": {"name": "adds numbers", "nesting": 0, "testNumber": 1, "details": {"duration_ms": 1.0}}}),
        json.dumps({"type": "test:pass", "data": {"name": "handles zero", "nesting": 0, "testNumber": 2, "details": {"duration_ms": 0.5}}}),
        json.dumps({"type": "test:diagnostic", "data": {"nesting": 0, "message": "tests 2"}}),
        json.dumps({"type": "test:diagnostic", "data": {"nesting": 0, "message": "pass 2"}}),
        json.dumps({"type": "test:diagnostic", "data": {"nesting": 0, "message": "fail 0"}}),
    ])

    runner = NodeTestRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=ndjson))):
        batch = await runner.run([], run_id="run-001")

    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []


@pytest.mark.asyncio
async def test_node_test_run_json_one_failure(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "node --test"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    ndjson = "\n".join([
        json.dumps({"type": "test:pass", "data": {"name": "passes", "nesting": 0, "testNumber": 1, "details": {"duration_ms": 1.0}}}),
        json.dumps({"type": "test:fail", "data": {"name": "fails", "nesting": 0, "testNumber": 2, "details": {"duration_ms": 0.5, "error": {"message": "Expected 1 to equal 2", "code": "ERR_ASSERTION"}}}}),
    ])

    runner = NodeTestRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=ndjson))):
        batch = await runner.run([], run_id="run-002")

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    f = batch.findings[0]
    assert f.kind == FindingKind.TEST_FAILURE
    assert f.severity == Severity.HIGH
    assert "Expected 1 to equal 2" in f.message


@pytest.mark.asyncio
async def test_node_test_run_skip_counted(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, scripts={"test": "node --test"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    ndjson = "\n".join([
        json.dumps({"type": "test:pass", "data": {"name": "passes", "nesting": 0, "testNumber": 1, "details": {"duration_ms": 1.0}}}),
        json.dumps({"type": "test:skip", "data": {"name": "skipped", "nesting": 0, "testNumber": 2, "details": {"duration_ms": 0.0}}}),
    ])

    runner = NodeTestRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=ndjson))):
        batch = await runner.run([], run_id="run-003")

    assert batch.tests_passed == 1
    assert batch.tests_skipped == 1
    assert batch.tests_failed == 0


@pytest.mark.asyncio
async def test_node_test_run_falls_back_to_tap(tmp_path: Path, monkeypatch) -> None:
    """If JSON parsing fails, the runner retries with TAP reporter."""
    _pkg(tmp_path, scripts={"test": "node --test"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    tap_output = "TAP version 13\nok 1 - works\nnot ok 2 - fails\n"

    call_count = 0

    async def mock_shell_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if "--test-reporter=json" in cmd:
            return _shell(stdout="not json at all", stderr="")
        if "--test-reporter=tap" in cmd:
            return _shell(stdout=tap_output)
        return _shell(stdout="")

    runner = NodeTestRunner(tmp_path)
    with patch.object(runner, "shell_run", new=mock_shell_run):
        batch = await runner.run([], run_id="run-004")

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert call_count == 2  # JSON attempt + TAP retry


# ---------------------------------------------------------------------------
# MochaRunner -- discovery
# ---------------------------------------------------------------------------


def test_mocha_discover_false_no_mocha(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = MochaRunner(tmp_path)
    assert runner.discover() is False


def test_mocha_discover_via_package_json_dep(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"mocha": "^10.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = MochaRunner(tmp_path)
    assert runner.discover() is True


def test_mocha_discover_via_mocharc(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path)
    (tmp_path / ".mocharc.yml").write_text("spec: test/**/*.js\n")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = MochaRunner(tmp_path)
    assert runner.discover() is True


def test_mocha_discover_raises_if_no_npx(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"mocha": "^10.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: None)
    runner = MochaRunner(tmp_path)
    with pytest.raises(RunnerNotAvailable):
        runner.discover()


# ---------------------------------------------------------------------------
# MochaRunner -- JSON output parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mocha_run_all_pass(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"mocha": "^10.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    mocha_json = json.dumps({
        "stats": {"passes": 3, "failures": 0, "pending": 0},
        "passes": [
            {"title": "t1", "fullTitle": "suite t1"},
            {"title": "t2", "fullTitle": "suite t2"},
            {"title": "t3", "fullTitle": "suite t3"},
        ],
        "failures": [],
        "pending": [],
    })

    runner = MochaRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=mocha_json))):
        batch = await runner.run([], run_id="run-mocha-pass")

    assert batch.tests_passed == 3
    assert batch.tests_failed == 0
    assert batch.findings == []


@pytest.mark.asyncio
async def test_mocha_run_one_failure(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"mocha": "^10.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    mocha_json = json.dumps({
        "stats": {"passes": 1, "failures": 1, "pending": 1},
        "passes": [{"title": "passes", "fullTitle": "suite passes"}],
        "failures": [{
            "title": "fails",
            "fullTitle": "suite fails",
            "file": "/path/to/test.js",
            "err": {"message": "AssertionError: 1 != 2", "stack": "..."},
        }],
        "pending": [{"title": "todo", "fullTitle": "suite todo"}],
    })

    runner = MochaRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=mocha_json))):
        batch = await runner.run([], run_id="run-mocha-fail")

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert batch.tests_skipped == 1
    assert len(batch.findings) == 1
    f = batch.findings[0]
    assert f.kind == FindingKind.TEST_FAILURE
    assert "AssertionError" in f.message
    assert f.rule_id == "mocha::suite fails"


@pytest.mark.asyncio
async def test_mocha_run_crash_returns_crash_batch(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"mocha": "^10.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    runner = MochaRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout="", stderr="Cannot find module"))):
        batch = await runner.run([], run_id="run-mocha-crash")

    assert batch.tests_failed == 1
    assert "mocha crashed" in batch.summary_line


# ---------------------------------------------------------------------------
# AvaRunner -- discovery
# ---------------------------------------------------------------------------


def test_ava_discover_false_no_ava(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = AvaRunner(tmp_path)
    assert runner.discover() is False


def test_ava_discover_via_package_json_dep(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"ava": "^6.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = AvaRunner(tmp_path)
    assert runner.discover() is True


def test_ava_discover_via_config_file(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path)
    (tmp_path / "ava.config.mjs").write_text("export default { files: ['tests/**/*'] }\n")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")
    runner = AvaRunner(tmp_path)
    assert runner.discover() is True


def test_ava_discover_raises_if_no_npx(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"ava": "^6.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: None)
    runner = AvaRunner(tmp_path)
    with pytest.raises(RunnerNotAvailable):
        runner.discover()


# ---------------------------------------------------------------------------
# AvaRunner -- TAP parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ava_run_all_pass(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"ava": "^6.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    tap = "TAP version 13\nok 1 - adds numbers\nok 2 - handles edge case\n# tests 2\n# pass 2\n# fail 0\n"

    runner = AvaRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=tap))):
        batch = await runner.run([], run_id="run-ava-pass")

    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []


@pytest.mark.asyncio
async def test_ava_run_one_failure(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"ava": "^6.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    tap = (
        "TAP version 13\n"
        "ok 1 - passes\n"
        "not ok 2 - fails\n"
        "  ---\n"
        "  message: 'Expected 1 to equal 2'\n"
        "  ...\n"
        "# tests 2\n# pass 1\n# fail 1\n"
    )

    runner = AvaRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=tap))):
        batch = await runner.run([], run_id="run-ava-fail")

    assert batch.tests_passed == 1
    assert batch.tests_failed == 1
    assert len(batch.findings) == 1
    assert "Expected 1 to equal 2" in batch.findings[0].message


# ---------------------------------------------------------------------------
# TapeRunner -- discovery
# ---------------------------------------------------------------------------


def test_tape_discover_false_no_tape(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = TapeRunner(tmp_path)
    assert runner.discover() is False


def test_tape_discover_via_package_json_dep(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"tape": "^5.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")
    runner = TapeRunner(tmp_path)
    assert runner.discover() is True


def test_tape_discover_false_no_node(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"tape": "^5.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: None)
    runner = TapeRunner(tmp_path)
    assert runner.discover() is False


# ---------------------------------------------------------------------------
# TapeRunner -- TAP parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tape_run_all_pass(tmp_path: Path, monkeypatch) -> None:
    _pkg(tmp_path, devDependencies={"tape": "^5.0.0"})
    test_dir = tmp_path / "test"
    test_dir.mkdir()
    test_file = test_dir / "test-add.js"
    test_file.write_text("// tape test\n")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    tap = "TAP version 13\n# addition\nok 1 adds two numbers\nok 2 handles zero\n1..2\n"

    runner = TapeRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=tap))):
        batch = await runner.run([], run_id="run-tape-pass")

    assert batch.tests_passed == 2
    assert batch.tests_failed == 0
    assert batch.findings == []


@pytest.mark.asyncio
async def test_tape_run_no_test_files(tmp_path: Path, monkeypatch) -> None:
    """Empty project with tape dep but no test files -> 0/0 summary."""
    _pkg(tmp_path, devDependencies={"tape": "^5.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    runner = TapeRunner(tmp_path)
    batch = await runner.run([], run_id="run-tape-empty")

    assert batch.tests_passed == 0
    assert batch.tests_failed == 0
    assert "0/0" in batch.summary_line


@pytest.mark.asyncio
async def test_tape_run_specific_test_ids(tmp_path: Path, monkeypatch) -> None:
    """When test_ids provided, only those files are run."""
    _pkg(tmp_path, devDependencies={"tape": "^5.0.0"})
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/node")

    tap = "TAP version 13\nok 1 my test\n"

    runner = TapeRunner(tmp_path)
    with patch.object(runner, "shell_run", new=AsyncMock(return_value=_shell(stdout=tap))) as mock_run:
        batch = await runner.run(["test/test-specific.js"], run_id="run-tape-specific")

    assert batch.tests_passed == 1
    # Verify the specific file was passed to shell_run
    call_args = mock_run.call_args[0][0]
    assert "test-specific.js" in " ".join(call_args)


# ---------------------------------------------------------------------------
# Registry: all four new runners are registered
# ---------------------------------------------------------------------------


def test_new_runners_in_default_registry() -> None:
    from tailtest.core.runner.base import get_default_registry

    registry = get_default_registry()
    langs = registry.registered_languages()
    assert "node_test" in langs
    assert "mocha" in langs
    assert "ava" in langs
    assert "tape" in langs


# ---------------------------------------------------------------------------
# JSRunner still defers correctly (no regression)
# ---------------------------------------------------------------------------


def test_jsrunner_still_rejects_bare_test_dir_without_framework(tmp_path: Path, monkeypatch) -> None:
    """JSRunner must not discover a project that only has node --test signal."""
    from tailtest.core.runner.javascript import JSRunner

    _pkg(tmp_path, scripts={"test": "node --test tests/"})
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    (test_dir / "foo.test.ts").write_text("import test from 'node:test'; test('x', () => {})")
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/npx")

    runner = JSRunner(tmp_path)
    # JSRunner requires explicit vitest or jest signal -- node --test is not one.
    assert runner.discover() is False
