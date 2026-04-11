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

from tailtest.core.config import DepthMode
from tailtest.core.config.schema import Config
from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.hook.post_tool_use import (
    _extract_diff_text,
    _extract_file_paths,
    _format_additional_context,
    _is_manifest_file,
    _is_self_edit,
    _looks_like_test_file,
    _parse_stdin,
    _should_invoke_redteam,
    _should_invoke_validator,
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


def test_parse_stdin_logs_diagnostic_on_malformed_json(caplog) -> None:
    """Phase 2 Task 2.10 follow-up: malformed JSON must not be silent.

    Level 2 dogfood (other session) flagged that
    ``_parse_stdin`` previously returned None on malformed JSON
    with no log line, making "hook crashed parsing input" and
    "hook not installed" indistinguishable. The fix is to emit
    one INFO line per failure mode so a user running with
    ``claude --debug`` can tell which case fired.
    """
    import logging

    with caplog.at_level(logging.INFO, logger="tailtest.hook.post_tool_use"):
        result = _parse_stdin("{not json")
    assert result is None
    assert any("not valid JSON" in r.message for r in caplog.records)


def test_parse_stdin_logs_diagnostic_on_non_object(caplog) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="tailtest.hook.post_tool_use"):
        result = _parse_stdin("[1, 2, 3]")
    assert result is None
    assert any("not an object" in r.message for r in caplog.records)


def test_parse_stdin_silent_on_empty_input(caplog) -> None:
    """Empty stdin must stay silent. Claude Code regularly invokes
    hooks with no payload as part of normal operation, and a log
    line on every empty call would flood the user's debug output.
    """
    import logging

    with caplog.at_level(logging.INFO, logger="tailtest.hook.post_tool_use"):
        result = _parse_stdin("")
    assert result is None
    assert not any(
        "not valid JSON" in r.message or "not an object" in r.message for r in caplog.records
    )


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


# --- Phase 2 Task 2.5: Security phase integration ----------------------


class _StubGitleaks:
    """Drop-in replacement for GitleaksRunner in the security phase tests.

    Every instance records the files it was asked to scan so tests
    can assert call count and argument shape. ``available`` and
    ``hits`` are class-level so tests can tweak them without
    constructing the stub themselves.
    """

    available = True
    hits: list[Finding] = []
    calls: list[list[Path]] = []

    def __init__(self, project_root: Path, **_: object) -> None:
        self.project_root = project_root

    def is_available(self) -> bool:
        return type(self).available

    async def scan(self, files: list[Path], *, run_id: str) -> list[Finding]:
        type(self).calls.append(list(files))
        _ = run_id
        return list(type(self).hits)


class _StubSemgrep:
    available = True
    hits: list[Finding] = []
    calls: list[list[Path]] = []

    def __init__(self, project_root: Path, **_: object) -> None:
        self.project_root = project_root

    def is_available(self) -> bool:
        return type(self).available

    async def scan(self, files: list[Path], *, run_id: str) -> list[Finding]:
        type(self).calls.append(list(files))
        _ = run_id
        return list(type(self).hits)


class _StubOSVLookup:
    hits: list[Finding] = []
    calls: list[object] = []

    def __init__(self, project_root: Path, **_: object) -> None:
        self.project_root = project_root

    async def check_manifest_diff(self, diff, *, run_id: str) -> list[Finding]:  # noqa: ANN001
        type(self).calls.append(diff)
        _ = run_id
        return list(type(self).hits)


def _reset_security_stubs() -> None:
    """Clear the class-level recorders between tests.

    Python class attributes persist across test functions; without
    this reset the second test sees the first test's call history.
    """
    _StubGitleaks.available = True
    _StubGitleaks.hits = []
    _StubGitleaks.calls = []
    _StubSemgrep.available = True
    _StubSemgrep.hits = []
    _StubSemgrep.calls = []
    _StubOSVLookup.hits = []
    _StubOSVLookup.calls = []


def _patch_security_scanners(monkeypatch) -> None:
    """Swap the three scanner classes for stubs inside the hook module."""
    monkeypatch.setattr("tailtest.hook.post_tool_use.GitleaksRunner", _StubGitleaks)
    monkeypatch.setattr("tailtest.hook.post_tool_use.SemgrepRunner", _StubSemgrep)
    monkeypatch.setattr("tailtest.hook.post_tool_use.OSVLookup", _StubOSVLookup)


def _make_security_finding(kind: FindingKind, *, run_id: str = "r1") -> Finding:
    return Finding.create(
        kind=kind,
        severity=Severity.HIGH,
        file="src/app.py",
        line=5,
        message=f"stub {kind.value} finding",
        run_id=run_id,
    )


@pytest.mark.asyncio
async def test_security_phase_off_depth_returns_empty(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    # Populate hits so we can confirm they stay unused.
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET)]
    _StubSemgrep.hits = [_make_security_finding(FindingKind.SAST)]

    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[tmp_path / "a.py"],
        config=Config(),
        depth=DepthMode.OFF,
        run_id="r1",
    )
    assert findings == []
    assert _StubGitleaks.calls == []
    assert _StubSemgrep.calls == []


@pytest.mark.asyncio
async def test_security_phase_quick_runs_only_gitleaks(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    secret = _make_security_finding(FindingKind.SECRET)
    sast = _make_security_finding(FindingKind.SAST)
    _StubGitleaks.hits = [secret]
    _StubSemgrep.hits = [sast]

    changed = [tmp_path / "src" / "app.py"]
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=changed,
        config=Config(),
        depth=DepthMode.QUICK,
        run_id="r-quick",
    )
    assert len(findings) == 1
    assert findings[0].kind == FindingKind.SECRET
    assert _StubGitleaks.calls == [changed]
    assert _StubSemgrep.calls == []


@pytest.mark.asyncio
async def test_security_phase_standard_runs_gitleaks_and_semgrep(
    tmp_path: Path, monkeypatch
) -> None:
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    secret = _make_security_finding(FindingKind.SECRET)
    sast = _make_security_finding(FindingKind.SAST)
    _StubGitleaks.hits = [secret]
    _StubSemgrep.hits = [sast]

    changed = [tmp_path / "src" / "app.py"]
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=changed,
        config=Config(),
        depth=DepthMode.STANDARD,
        run_id="r-std",
    )
    assert len(findings) == 2
    kinds = {f.kind for f in findings}
    assert kinds == {FindingKind.SECRET, FindingKind.SAST}
    assert _StubGitleaks.calls == [changed]
    assert _StubSemgrep.calls == [changed]
    # No manifest edits, so OSV was never invoked.
    assert _StubOSVLookup.calls == []


@pytest.mark.asyncio
async def test_security_phase_osv_runs_on_manifest_edit(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubOSVLookup.hits = [_make_security_finding(FindingKind.SCA)]

    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "ex"\nversion = "0.1.0"\ndependencies = ["click>=8.1"]\n'
    )
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[manifest],
        config=Config(),
        depth=DepthMode.STANDARD,
        run_id="r-sca",
    )
    sca_findings = [f for f in findings if f.kind == FindingKind.SCA]
    assert len(sca_findings) == 1
    # OSVLookup should have been queried exactly once.
    assert len(_StubOSVLookup.calls) == 1
    # Snapshot should now exist for subsequent runs.
    snap = tmp_path / ".tailtest" / "cache" / "manifests" / "pyproject.toml.snap"
    assert snap.exists()


@pytest.mark.asyncio
async def test_security_phase_osv_second_run_uses_snapshot(tmp_path: Path, monkeypatch) -> None:
    """Second hook run on the same manifest should diff against snapshot.

    After the first run saves the snapshot, a subsequent run with
    the same manifest content yields an empty diff and therefore
    does not call OSV at all.
    """
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubOSVLookup.hits = [_make_security_finding(FindingKind.SCA)]

    manifest = tmp_path / "pyproject.toml"
    manifest.write_text(
        '[project]\nname = "ex"\nversion = "0.1.0"\ndependencies = ["click>=8.1"]\n'
    )

    first_cfg = Config()
    # First run: OSV called, findings returned.
    first = await _run_security_phase(
        root=tmp_path,
        changed_files=[manifest],
        config=first_cfg,
        depth=DepthMode.STANDARD,
        run_id="r-1",
    )
    assert any(f.kind == FindingKind.SCA for f in first)
    assert len(_StubOSVLookup.calls) == 1

    # Second run on the unchanged manifest: diff is empty, OSV is
    # NOT called, so no SCA findings even though the stub had hits
    # queued.
    second = await _run_security_phase(
        root=tmp_path,
        changed_files=[manifest],
        config=first_cfg,
        depth=DepthMode.STANDARD,
        run_id="r-2",
    )
    assert [f for f in second if f.kind == FindingKind.SCA] == []
    assert len(_StubOSVLookup.calls) == 1  # still 1, no new call


@pytest.mark.asyncio
async def test_security_phase_respects_secrets_disabled(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import Config, DepthMode, SecurityConfig
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET)]
    _StubSemgrep.hits = [_make_security_finding(FindingKind.SAST)]

    from tailtest.core.config import SastConfig, ScaConfig

    config = Config(
        security=SecurityConfig(
            secrets=False,
            sast=SastConfig(enabled=True),
            sca=ScaConfig(enabled=True),
        )
    )
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[tmp_path / "app.py"],
        config=config,
        depth=DepthMode.STANDARD,
        run_id="r-no-secrets",
    )
    # Only SAST finding made it through.
    assert [f.kind for f in findings] == [FindingKind.SAST]
    assert _StubGitleaks.calls == []
    assert _StubSemgrep.calls == [[tmp_path / "app.py"]]


@pytest.mark.asyncio
async def test_security_phase_respects_sast_disabled(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import (
        Config,
        DepthMode,
        SastConfig,
        ScaConfig,
        SecurityConfig,
    )
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET)]
    _StubSemgrep.hits = [_make_security_finding(FindingKind.SAST)]

    config = Config(
        security=SecurityConfig(
            secrets=True,
            sast=SastConfig(enabled=False),
            sca=ScaConfig(enabled=True),
        )
    )
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[tmp_path / "app.py"],
        config=config,
        depth=DepthMode.STANDARD,
        run_id="r-no-sast",
    )
    assert [f.kind for f in findings] == [FindingKind.SECRET]
    assert _StubSemgrep.calls == []


@pytest.mark.asyncio
async def test_security_phase_respects_sca_disabled(tmp_path: Path, monkeypatch) -> None:
    from tailtest.core.config import (
        Config,
        DepthMode,
        SastConfig,
        ScaConfig,
        SecurityConfig,
    )
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubOSVLookup.hits = [_make_security_finding(FindingKind.SCA)]

    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname = "ex"\nversion = "0.1.0"\n')

    config = Config(
        security=SecurityConfig(
            secrets=True,
            sast=SastConfig(enabled=True),
            sca=ScaConfig(enabled=False),
        )
    )
    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[manifest],
        config=config,
        depth=DepthMode.STANDARD,
        run_id="r-no-sca",
    )
    assert [f for f in findings if f.kind == FindingKind.SCA] == []
    assert _StubOSVLookup.calls == []


@pytest.mark.asyncio
async def test_security_phase_skipped_when_scanner_unavailable(tmp_path: Path, monkeypatch) -> None:
    """Missing gitleaks/semgrep binaries should not break the hot loop."""
    from tailtest.core.config import Config, DepthMode
    from tailtest.hook.post_tool_use import _run_security_phase

    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubGitleaks.available = False
    _StubSemgrep.available = False
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET)]

    findings = await _run_security_phase(
        root=tmp_path,
        changed_files=[tmp_path / "app.py"],
        config=Config(),
        depth=DepthMode.STANDARD,
        run_id="r-unavailable",
    )
    assert findings == []
    # scan() should not have been called because is_available() was
    # False.
    assert _StubGitleaks.calls == []
    assert _StubSemgrep.calls == []


# --- Summary line rebuild ----------------------------------------------


def test_build_summary_line_no_security_findings() -> None:
    from tailtest.hook.post_tool_use import _build_summary_line

    batch = FindingBatch(
        run_id="r",
        depth="standard",
        tests_passed=14,
        tests_failed=0,
        tests_skipped=0,
    )
    assert _build_summary_line(batch, 1.8) == ("tailtest: 14/14 tests passed · 1.8s")


def test_build_summary_line_with_one_new_security_issue() -> None:
    from tailtest.hook.post_tool_use import _build_summary_line

    secret = Finding.create(
        kind=FindingKind.SECRET,
        severity=Severity.HIGH,
        file="x.py",
        line=3,
        message="api key",
        run_id="r",
    )
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        tests_passed=14,
        tests_failed=0,
        tests_skipped=0,
        findings=[secret],
    )
    assert _build_summary_line(batch, 1.8) == (
        "tailtest: 14/14 tests passed · 1 new security issue · 1.8s"
    )


def test_build_summary_line_with_multiple_security_issues() -> None:
    from tailtest.hook.post_tool_use import _build_summary_line

    findings = [
        Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.HIGH,
            file="x.py",
            line=1,
            message="secret 1",
            run_id="r",
        ),
        Finding.create(
            kind=FindingKind.SAST,
            severity=Severity.MEDIUM,
            file="x.py",
            line=5,
            message="sast hit",
            run_id="r",
        ),
        Finding.create(
            kind=FindingKind.SCA,
            severity=Severity.HIGH,
            file="pyproject.toml",
            line=0,
            message="vuln dep",
            run_id="r",
        ),
    ]
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        tests_passed=3,
        tests_failed=0,
        findings=findings,
    )
    assert _build_summary_line(batch, 2.5) == (
        "tailtest: 3/3 tests passed · 3 new security issues · 2.5s"
    )


def test_build_summary_line_ignores_baseline_security_findings() -> None:
    """Findings marked ``in_baseline=True`` do not count as new."""
    from tailtest.hook.post_tool_use import _build_summary_line

    new_finding = Finding.create(
        kind=FindingKind.SECRET,
        severity=Severity.HIGH,
        file="x.py",
        line=1,
        message="new",
        run_id="r",
    )
    old_finding = Finding.create(
        kind=FindingKind.SAST,
        severity=Severity.HIGH,
        file="x.py",
        line=2,
        message="old",
        run_id="r",
    ).model_copy(update={"in_baseline": True})
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        tests_passed=1,
        findings=[new_finding, old_finding],
    )
    summary = _build_summary_line(batch, 1.0)
    assert "1 new security issue" in summary
    assert "2 new" not in summary


def test_build_summary_line_with_failing_tests_and_security() -> None:
    from tailtest.hook.post_tool_use import _build_summary_line

    batch = FindingBatch(
        run_id="r",
        depth="standard",
        tests_passed=3,
        tests_failed=2,
        tests_skipped=0,
    )
    assert _build_summary_line(batch, 0.7) == ("tailtest: 3/5 tests passed · 2 failed · 0.7s")


# --- Manifest snapshot round-trip (module-private) --------------------


def test_manifest_snapshot_round_trip(tmp_path: Path) -> None:
    from tailtest.hook.post_tool_use import (
        _load_manifest_snapshot,
        _save_manifest_snapshot,
    )
    from tailtest.security.sca.manifests import PackageRef

    cache_dir = tmp_path / ".tailtest" / "cache" / "manifests"
    refs = [
        PackageRef("click", ">=8.1", "PyPI", "project.dependencies"),
        PackageRef("httpx", ">=0.27", "PyPI", "project.dependencies"),
    ]
    _save_manifest_snapshot(cache_dir, "pyproject.toml", refs)
    loaded = _load_manifest_snapshot(cache_dir, "pyproject.toml")
    assert len(loaded) == 2
    assert loaded[0].name == "click"
    assert loaded[0].version == ">=8.1"
    assert loaded[0].ecosystem == "PyPI"
    assert loaded[0].source_spec == "project.dependencies"
    assert loaded[1].name == "httpx"


def test_manifest_snapshot_missing_file_returns_empty(tmp_path: Path) -> None:
    from tailtest.hook.post_tool_use import _load_manifest_snapshot

    assert _load_manifest_snapshot(tmp_path, "missing.toml") == []


def test_manifest_snapshot_malformed_file_returns_empty(tmp_path: Path) -> None:
    from tailtest.hook.post_tool_use import _load_manifest_snapshot

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "pyproject.toml.snap").write_text("{not json")
    assert _load_manifest_snapshot(tmp_path, "pyproject.toml") == []


def test_manifest_snapshot_wrong_shape_returns_empty(tmp_path: Path) -> None:
    from tailtest.hook.post_tool_use import _load_manifest_snapshot

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "pyproject.toml.snap").write_text('{"not": "a list"}')
    assert _load_manifest_snapshot(tmp_path, "pyproject.toml") == []


# --- End-to-end: security findings merge into run() output ------------


@pytest.mark.asyncio
async def test_run_end_to_end_merges_security_findings(tmp_path: Path, monkeypatch) -> None:
    """Security findings from stubbed scanners flow into the final envelope.

    Uses a minimal Python fixture where all tests pass so the
    security phase actually runs. Stubs the three scanners to
    return one SECRET + one SAST finding each, and asserts both
    appear in the ``additionalContext`` block along with an
    updated summary line counting ``2 new security issues``.
    """
    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET, run_id="e2e")]
    _StubSemgrep.hits = [_make_security_finding(FindingKind.SAST, run_id="e2e")]

    # All-passing pytest fixture so the security phase actually fires.
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\npythonpath = ["src"]\n'
    )
    src = tmp_path / "src" / "widget"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "math_ops.py").write_text("def add(a, b):\n    return a + b\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_math_ops.py").write_text(
        "from widget.math_ops import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )

    changed = tmp_path / "src" / "widget" / "math_ops.py"
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(changed)}})
    result = await run(payload, project_root=tmp_path)

    assert result.stdout_json is not None, result.reason
    envelope = json.loads(result.stdout_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    # Summary reflects the security count and the total duration.
    assert "2 new security issues" in ctx
    # The scanners were actually invoked.
    assert len(_StubGitleaks.calls) == 1
    assert len(_StubSemgrep.calls) == 1


@pytest.mark.asyncio
async def test_run_end_to_end_skips_security_on_test_failure(tmp_path: Path, monkeypatch) -> None:
    """Security scanners must NOT run when tests fail (test-first UX).

    Uses the existing failing-test fixture and stubs the scanners.
    Verifies none of the stub scanners were called even though the
    config defaults have them enabled.
    """
    _reset_security_stubs()
    _patch_security_scanners(monkeypatch)
    _StubGitleaks.hits = [_make_security_finding(FindingKind.SECRET)]
    _StubSemgrep.hits = [_make_security_finding(FindingKind.SAST)]

    changed = _make_minimal_python_fixture(tmp_path)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(changed)}})
    result = await run(payload, project_root=tmp_path)

    assert result.stdout_json is not None, result.reason
    envelope = json.loads(result.stdout_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    # Security findings should NOT appear when tests failed.
    assert "security" not in ctx.lower()
    assert _StubGitleaks.calls == []
    assert _StubSemgrep.calls == []


# --- Task 5.6: depth-mode gating for validator ---------------------------


def _green_batch() -> FindingBatch:
    return FindingBatch(run_id="r1", depth="standard", tests_passed=5, tests_failed=0)


def _red_batch() -> FindingBatch:
    return FindingBatch(run_id="r1", depth="standard", tests_passed=0, tests_failed=2)


def _cfg(depth: DepthMode, *, validator_enabled: bool = True) -> Config:
    return Config(depth=depth, validator_enabled=validator_enabled)


def test_validator_off_at_off_depth() -> None:
    assert not _should_invoke_validator(_cfg(DepthMode.OFF), _green_batch())


def test_validator_off_at_quick_depth() -> None:
    assert not _should_invoke_validator(_cfg(DepthMode.QUICK), _green_batch())


def test_validator_off_at_standard_depth() -> None:
    assert not _should_invoke_validator(_cfg(DepthMode.STANDARD), _green_batch())


def test_validator_fires_at_thorough_when_green() -> None:
    assert _should_invoke_validator(_cfg(DepthMode.THOROUGH), _green_batch())


def test_validator_does_not_fire_at_thorough_when_red() -> None:
    assert not _should_invoke_validator(_cfg(DepthMode.THOROUGH), _red_batch())


def test_validator_fires_at_paranoid_when_green() -> None:
    assert _should_invoke_validator(_cfg(DepthMode.PARANOID), _green_batch())


def test_validator_fires_at_paranoid_even_when_red() -> None:
    assert _should_invoke_validator(_cfg(DepthMode.PARANOID), _red_batch())


def test_validator_disabled_flag_blocks_all_depths() -> None:
    for depth in (DepthMode.THOROUGH, DepthMode.PARANOID):
        assert not _should_invoke_validator(_cfg(depth, validator_enabled=False), _green_batch())


def test_extract_diff_text_git_diff_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """When git diff HEAD succeeds, it takes priority over payload fallback."""
    import subprocess

    fake_result = subprocess.CompletedProcess(
        args=["git", "diff", "HEAD"],
        returncode=0,
        stdout="diff --git a/foo.py b/foo.py\n+new line\n",
        stderr="",
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_result)
    payload = {
        "tool_name": "Edit",
        "tool_input": {"old_string": "old code", "new_string": "new code"},
    }
    diff = _extract_diff_text(payload)
    assert "diff --git" in diff
    assert "new line" in diff
    assert "old code" not in diff  # payload fallback not used


def test_extract_diff_text_edit_payload_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to payload when git diff returns empty."""
    import subprocess

    fake_result = subprocess.CompletedProcess(
        args=["git", "diff", "HEAD"],
        returncode=0,
        stdout="",  # empty = no staged changes
        stderr="",
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_result)
    payload = {
        "tool_name": "Edit",
        "tool_input": {"old_string": "old code", "new_string": "new code"},
    }
    diff = _extract_diff_text(payload)
    assert "old code" in diff
    assert "new code" in diff


def test_extract_diff_text_write_payload_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to payload Write content when git not available."""
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no git")))
    payload = {
        "tool_name": "Write",
        "tool_input": {"content": "full file content here"},
    }
    diff = _extract_diff_text(payload)
    assert "full file content here" in diff


def test_extract_diff_text_unknown_tool_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no git")))
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    assert _extract_diff_text(payload) == ""


def test_format_additional_context_includes_validator_findings() -> None:
    main_batch = FindingBatch(run_id="r1", depth="standard", summary_line="tailtest: 3/3 passed")
    validator_finding = Finding.create(
        kind=FindingKind.VALIDATOR,
        severity=Severity.HIGH,
        file=Path("src/foo.py"),
        line=42,
        message="Possible null deref",
        run_id="r1",
        rule_id="validator::src/foo.py:42",
    )
    validator_finding = validator_finding.model_copy(
        update={"reasoning": "The check is missing a guard"}
    )
    vbatch = FindingBatch(
        run_id="r1",
        depth="thorough",
        findings=[validator_finding],
        summary_line="tailtest: validator found 1 issue(s)",
    )
    ctx_json = _format_additional_context(
        main_batch,
        manifest_rescanned=False,
        validator_batch=vbatch,
    )
    envelope = json.loads(ctx_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "validator" in ctx.lower()
    assert "Possible null deref" in ctx
    assert "guard" in ctx  # reasoning snippet


def test_format_additional_context_no_validator_batch() -> None:
    batch = FindingBatch(run_id="r1", depth="standard", summary_line="tailtest: ok")
    ctx_json = _format_additional_context(batch, manifest_rescanned=False)
    envelope = json.loads(ctx_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "validator" not in ctx


# --- Task 6.4: depth-mode gating for red-team ---------------------------


def _agent_profile(tmp_path: Path) -> Path:
    """Write a minimal profile that looks like an AI agent project."""
    from tailtest.core.scan.profile import AISurface, ProjectProfile

    profile = ProjectProfile(root=tmp_path, ai_surface=AISurface.AGENT)
    profile_dir = tmp_path / ".tailtest"
    profile_dir.mkdir(exist_ok=True)
    (profile_dir / "profile.json").write_text(profile.to_json())
    return tmp_path


def _none_profile(tmp_path: Path) -> Path:
    """Write a profile with ai_surface=none."""
    from tailtest.core.scan.profile import AISurface, ProjectProfile

    profile = ProjectProfile(root=tmp_path, ai_surface=AISurface.NONE)
    profile_dir = tmp_path / ".tailtest"
    profile_dir.mkdir(exist_ok=True)
    (profile_dir / "profile.json").write_text(profile.to_json())
    return tmp_path


def _rt_cfg(depth: DepthMode, *, ai_checks_enabled: bool | None = None) -> Config:
    return Config(depth=depth, ai_checks_enabled=ai_checks_enabled)


def test_redteam_off_at_off_depth(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.OFF), root)


def test_redteam_off_at_quick_depth(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.QUICK), root)


def test_redteam_off_at_standard_depth(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.STANDARD), root)


def test_redteam_off_at_thorough_depth(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.THOROUGH), root)


def test_redteam_fires_at_paranoid_for_agent(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert _should_invoke_redteam(_rt_cfg(DepthMode.PARANOID), root)


def test_redteam_off_for_non_agent_at_paranoid(tmp_path: Path) -> None:
    root = _none_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.PARANOID), root)


def test_redteam_off_when_ai_checks_explicitly_false(tmp_path: Path) -> None:
    root = _agent_profile(tmp_path)
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.PARANOID, ai_checks_enabled=False), root)


def test_redteam_fires_when_ai_checks_none(tmp_path: Path) -> None:
    # ai_checks_enabled=None means "not explicitly disabled"
    root = _agent_profile(tmp_path)
    assert _should_invoke_redteam(_rt_cfg(DepthMode.PARANOID, ai_checks_enabled=None), root)


def test_redteam_off_when_no_profile(tmp_path: Path) -> None:
    # No .tailtest/profile.json -- load_profile returns None
    assert not _should_invoke_redteam(_rt_cfg(DepthMode.PARANOID), tmp_path)


def test_format_additional_context_includes_redteam_findings() -> None:
    main_batch = FindingBatch(run_id="r1", depth="paranoid", summary_line="tailtest: 3/3 passed")
    rt_finding = Finding.create(
        kind=FindingKind.REDTEAM,
        severity=Severity.HIGH,
        file=Path("(agent entry point)"),
        line=0,
        message="[prompt_injection] Ignore-previous: agent lacks sanitization",
        run_id="redteam",
        rule_id="redteam/prompt_injection/pi_001",
    )
    rt_finding = rt_finding.model_copy(update={"reasoning": "No input validation present"})
    rbatch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[rt_finding],
        summary_line="red-team: 1 vulnerability findings across 64 attacks (8 categories).",
    )
    ctx_json = _format_additional_context(
        main_batch,
        manifest_rescanned=False,
        redteam_batch=rbatch,
    )
    envelope = json.loads(ctx_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "red-team" in ctx.lower()
    assert "prompt_injection" in ctx
    assert "No input validation present" in ctx


def test_format_additional_context_no_redteam_batch() -> None:
    batch = FindingBatch(run_id="r1", depth="standard", summary_line="tailtest: ok")
    ctx_json = _format_additional_context(batch, manifest_rescanned=False)
    envelope = json.loads(ctx_json)
    ctx = envelope["hookSpecificOutput"]["additionalContext"]
    assert "red-team" not in ctx
