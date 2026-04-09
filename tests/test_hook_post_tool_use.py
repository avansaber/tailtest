"""Tests for the PostToolUse hook runtime (Phase 1 Task 1.5).

Exercises the pure-function helpers (parsing, extraction, test-file
detection, self-edit exclusion, manifest detection, formatting,
truncation) in isolation, then end-to-end via ``run()`` with the
engine pointed at temporary fixture projects.

The end-to-end tests construct minimal Python projects in tmp_path
and use the real PythonRunner, so we exercise the full pipeline
without mocking the runner. The only thing mocked is stdin text,
which is passed as a function argument rather than read from
sys.stdin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.hook.post_tool_use import (
    _extract_file_paths,
    _format_additional_context,
    _is_manifest_file,
    _is_self_edit,
    _looks_like_test_file,
    _parse_stdin,
    _truncate,
    run,
)

# --- Pure parser helpers ------------------------------------------------


def test_parse_stdin_accepts_valid_json() -> None:
    data = _parse_stdin('{"tool_name": "Edit", "tool_input": {"file_path": "foo.py"}}')
    assert data is not None
    assert data["tool_name"] == "Edit"


def test_parse_stdin_returns_none_for_empty() -> None:
    assert _parse_stdin("") is None
    assert _parse_stdin("   \n") is None


def test_parse_stdin_returns_none_for_malformed_json() -> None:
    assert _parse_stdin("{not json") is None


def test_parse_stdin_returns_none_for_non_dict_payload() -> None:
    assert _parse_stdin('"just a string"') is None
    assert _parse_stdin("[1, 2, 3]") is None


# --- File path extraction ----------------------------------------------


def test_extract_file_paths_edit_payload() -> None:
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "/tmp/x/foo.py", "old_string": "a", "new_string": "b"},
    }
    paths = _extract_file_paths(payload)
    assert paths == [Path("/tmp/x/foo.py")]


def test_extract_file_paths_write_payload() -> None:
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/x/foo.py", "content": "..."},
    }
    paths = _extract_file_paths(payload)
    assert paths == [Path("/tmp/x/foo.py")]


def test_extract_file_paths_empty_when_missing() -> None:
    assert _extract_file_paths({}) == []
    assert _extract_file_paths({"tool_input": {}}) == []
    assert _extract_file_paths({"tool_input": "not a dict"}) == []


# --- Test file detection + self-edit exclusion -------------------------


def test_looks_like_test_file_python() -> None:
    assert _looks_like_test_file(Path("tests/test_widget.py"))
    assert _looks_like_test_file(Path("tests/unit/test_widget.py"))
    assert not _looks_like_test_file(Path("src/widget.py"))


def test_looks_like_test_file_js_ts() -> None:
    assert _looks_like_test_file(Path("src/widget.test.ts"))
    assert _looks_like_test_file(Path("src/widget.spec.tsx"))
    assert _looks_like_test_file(Path("src/widget.test.js"))
    assert not _looks_like_test_file(Path("src/widget.ts"))


def test_self_edit_exclusion_matches_tailtest_src() -> None:
    """Files inside the tailtest source tree must be flagged as self-edits."""
    assert _is_self_edit(Path("/Users/x/projects/tailtest/src/tailtest/core/scan/scanner.py"))
    assert _is_self_edit(Path("/Users/x/projects/tailtest/tests/test_runner_python.py"))
    assert _is_self_edit(Path("/home/user/code/tailtest/src/tailtest/core/runner/python.py"))


def test_self_edit_exclusion_ignores_unrelated_paths() -> None:
    """Files outside tailtest's source tree must NOT be flagged as self-edits."""
    assert not _is_self_edit(Path("/Users/x/myproject/src/app.py"))
    assert not _is_self_edit(Path("/home/user/work/widget.ts"))


# --- Manifest file detection -------------------------------------------


def test_is_manifest_file_positive_cases() -> None:
    assert _is_manifest_file(Path("package.json"))
    assert _is_manifest_file(Path("/tmp/x/pyproject.toml"))
    assert _is_manifest_file(Path("Cargo.toml"))
    assert _is_manifest_file(Path("Gemfile"))
    assert _is_manifest_file(Path("Dockerfile"))
    assert _is_manifest_file(Path("go.mod"))
    assert _is_manifest_file(Path("requirements.txt"))
    assert _is_manifest_file(Path("vitest.config.ts"))
    assert _is_manifest_file(Path("jest.config.js"))


def test_is_manifest_file_negative_cases() -> None:
    assert not _is_manifest_file(Path("foo.py"))
    assert not _is_manifest_file(Path("README.md"))
    assert not _is_manifest_file(Path("foo.json"))  # not a known manifest name


# --- Output formatting --------------------------------------------------


def _fake_batch(
    findings: list[Finding] | None = None,
    *,
    summary: str = "tailtest: 5/5 tests passed",
) -> FindingBatch:
    return FindingBatch(
        run_id="r1",
        depth="standard",
        findings=findings or [],
        duration_ms=100.0,
        summary_line=summary,
        tests_passed=5,
    )


def test_format_additional_context_green_run_returns_json_envelope() -> None:
    batch = _fake_batch()
    out = _format_additional_context(batch, manifest_rescanned=False)
    envelope = json.loads(out)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "5/5 tests passed" in envelope["hookSpecificOutput"]["additionalContext"]


def test_format_additional_context_includes_manifest_rescan_note() -> None:
    batch = _fake_batch()
    out = _format_additional_context(batch, manifest_rescanned=True)
    envelope = json.loads(out)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "manifest rescan" in ctx


def test_format_additional_context_includes_findings() -> None:
    finding = Finding.create(
        kind=FindingKind.TEST_FAILURE,
        severity=Severity.HIGH,
        file=Path("tests/test_foo.py"),
        line=15,
        message="assert 1 == 2",
        run_id="r1",
        rule_id="pytest::test_foo::test_bad",
        claude_hint="assert 1 == 2",
    )
    batch = _fake_batch(findings=[finding], summary="tailtest: 4/5 tests passed, 1 failed")
    out = _format_additional_context(batch, manifest_rescanned=False)
    envelope = json.loads(out)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "1 failed" in ctx
    assert "tests/test_foo.py:15" in ctx
    assert "assert 1 == 2" in ctx
    assert "hint:" in ctx


def test_format_additional_context_truncates_to_top_5_findings() -> None:
    findings = [
        Finding.create(
            kind=FindingKind.TEST_FAILURE,
            severity=Severity.HIGH,
            file=Path(f"tests/test_{i}.py"),
            line=i,
            message=f"boom {i}",
            run_id="r1",
            rule_id=f"pytest::r{i}",
        )
        for i in range(10)
    ]
    batch = _fake_batch(findings=findings, summary="tailtest: 0/10 tests passed")
    out = _format_additional_context(batch, manifest_rescanned=False)
    envelope = json.loads(out)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    # Should mention the 5 truncation footer.
    assert "5 more findings" in ctx
    assert "latest.json" in ctx


def test_truncate_caps_at_5kb_with_footer() -> None:
    big = "x" * 10000
    out = _truncate(big)
    assert len(out.encode("utf-8")) <= 5 * 1024
    assert "truncated at 5KB" in out


def test_truncate_leaves_small_payload_unchanged() -> None:
    small = "x" * 100
    assert _truncate(small) == small


# --- End-to-end via run() -----------------------------------------------


def _make_minimal_python_fixture(tmp_path: Path) -> Path:
    """Build a minimal pytest project: 1 passing test, 1 failing test."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    src = tmp_path / "src" / "widget"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "math_ops.py").write_text(
        "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a + b  # buggy\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_math_ops.py").write_text(
        "from widget.math_ops import add, subtract\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n"
        "def test_subtract():\n    assert subtract(5, 3) == 2\n"
    )
    return tmp_path / "src" / "widget" / "math_ops.py"


@pytest.mark.asyncio
async def test_run_returns_none_for_empty_stdin(tmp_path: Path) -> None:
    result = await run("", project_root=tmp_path)
    assert result.stdout_json is None
    assert "stdin" in result.reason


@pytest.mark.asyncio
async def test_run_returns_none_for_unsupported_tool(tmp_path: Path) -> None:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    result = await run(payload, project_root=tmp_path)
    assert result.stdout_json is None
    assert "unsupported tool" in result.reason


@pytest.mark.asyncio
async def test_run_returns_none_for_missing_file_path(tmp_path: Path) -> None:
    payload = json.dumps({"tool_name": "Edit", "tool_input": {}})
    result = await run(payload, project_root=tmp_path)
    assert result.stdout_json is None
    assert "file_path" in result.reason


@pytest.mark.asyncio
async def test_run_skips_self_edits(tmp_path: Path) -> None:
    """Edits inside tailtest's own source tree short-circuit to None."""
    payload = json.dumps(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "/Users/x/projects/tailtest/src/tailtest/core/runner/python.py"
            },
        }
    )
    result = await run(payload, project_root=tmp_path)
    assert result.stdout_json is None
    assert "self-edit" in result.reason


@pytest.mark.asyncio
async def test_run_skips_when_all_changes_are_test_files(tmp_path: Path) -> None:
    """If every changed file is itself a test file, the hook emits nothing."""
    payload = json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": "/tmp/x/tests/test_x.py"}}
    )
    result = await run(payload, project_root=tmp_path)
    assert result.stdout_json is None
    assert "test files" in result.reason


@pytest.mark.asyncio
async def test_run_end_to_end_reports_failure(tmp_path: Path) -> None:
    """Hook runs impacted tests and emits a hookSpecificOutput JSON envelope."""
    changed = _make_minimal_python_fixture(tmp_path)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(changed)}})
    result = await run(payload, project_root=tmp_path)

    # The fixture has a failing test, so the hook must emit a response.
    assert result.stdout_json is not None, result.reason
    envelope = json.loads(result.stdout_json)
    assert envelope["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "test" in ctx.lower()
    # Persisted latest report exists.
    assert (tmp_path / ".tailtest" / "reports" / "latest.json").exists()


@pytest.mark.asyncio
async def test_run_handles_depth_off(tmp_path: Path) -> None:
    """Setting depth: off in config short-circuits the hook to emit nothing."""
    _make_minimal_python_fixture(tmp_path)
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    # YAML 1.1 parses bare `off` as boolean False, so quote it to force
    # the string value the config enum expects.
    (tailtest_dir / "config.yaml").write_text('schema_version: 1\ndepth: "off"\n')

    payload = json.dumps(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(tmp_path / "src" / "widget" / "math_ops.py")},
        }
    )
    result = await run(payload, project_root=tmp_path)
    assert result.stdout_json is None
    assert "off" in result.reason


@pytest.mark.asyncio
async def test_run_triggers_manifest_rescan(tmp_path: Path) -> None:
    """Editing pyproject.toml triggers a shallow rescan before tests run."""
    _make_minimal_python_fixture(tmp_path)
    payload = json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "pyproject.toml")}}
    )
    result = await run(payload, project_root=tmp_path)
    # Either None (no runner for a .toml file) or a successful response,
    # both are valid. The thing we care about is that the rescan wrote
    # profile.json.
    _ = result
    assert (tmp_path / ".tailtest" / "profile.json").exists()
