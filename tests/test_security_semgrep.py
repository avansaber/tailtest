"""Tests for SemgrepRunner (Phase 2 Task 2.2).

Pure JSON parsing tested with canned semgrep output. The
subprocess path tested with a mocked ``asyncio.create_subprocess_exec``
so the suite never depends on a real semgrep binary being on PATH.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.security.sast.semgrep import (
    DEFAULT_RULESET,
    SemgrepRunner,
    _raw_to_hit,
    _semgrep_severity_to_unified,
    _SemgrepHit,
    parse_semgrep_json,
)

# --- Canned Semgrep JSON outputs ---------------------------------------


_ONE_RESULT_JSON = json.dumps(
    {
        "results": [
            {
                "check_id": "python.lang.security.audit.exec-detected.exec-detected",
                "path": "src/app.py",
                "start": {"line": 15, "col": 5, "offset": 0},
                "end": {"line": 15, "col": 25, "offset": 20},
                "extra": {
                    "message": "Detected eval() which can execute arbitrary code. Avoid using eval on untrusted input.",
                    "severity": "ERROR",
                    "metadata": {
                        "cwe": [
                            "CWE-94: Improper Control of Generation of Code ('Code Injection')"
                        ],
                        "owasp": ["A03:2021 Injection"],
                        "references": [
                            "https://semgrep.dev/r/python.lang.security.audit.exec-detected"
                        ],
                    },
                },
            }
        ],
        "errors": [],
    }
)

_THREE_RESULTS_JSON = json.dumps(
    {
        "results": [
            {
                "check_id": "python.flask.security.audit.debug-enabled",
                "path": "src/app.py",
                "start": {"line": 3, "col": 1},
                "end": {"line": 3, "col": 20},
                "extra": {
                    "message": "Flask debug mode is enabled.",
                    "severity": "WARNING",
                    "metadata": {"cwe": ["CWE-489"]},
                },
            },
            {
                "check_id": "python.lang.correctness.common-mistakes.useless-print",
                "path": "src/app.py",
                "start": {"line": 42, "col": 5},
                "end": {"line": 42, "col": 25},
                "extra": {
                    "message": "This print statement has no effect.",
                    "severity": "INFO",
                    "metadata": {},
                },
            },
            {
                "check_id": "javascript.express.security.audit.xss.direct-response-write",
                "path": "src/routes.js",
                "start": {"line": 12, "col": 3},
                "end": {"line": 12, "col": 40},
                "extra": {
                    "message": "User input flows directly into response.write without sanitization.",
                    "severity": "ERROR",
                    "metadata": {
                        "owasp": "A03:2021 Injection",
                        "references": "https://owasp.org/www-community/attacks/xss/",
                    },
                },
            },
        ],
        "errors": [],
    }
)


# --- Pure parser --------------------------------------------------------


def test_parse_empty_string_returns_empty_list() -> None:
    assert parse_semgrep_json("") == []
    assert parse_semgrep_json("   \n") == []


def test_parse_malformed_json_returns_empty_list() -> None:
    assert parse_semgrep_json("{not json") == []


def test_parse_non_object_root_returns_empty_list() -> None:
    assert parse_semgrep_json("[1, 2, 3]") == []
    assert parse_semgrep_json('"just a string"') == []


def test_parse_missing_results_key_returns_empty_list() -> None:
    assert parse_semgrep_json(json.dumps({"errors": []})) == []


def test_parse_single_result_extracts_all_fields() -> None:
    hits = parse_semgrep_json(_ONE_RESULT_JSON)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, _SemgrepHit)
    assert hit.check_id == "python.lang.security.audit.exec-detected.exec-detected"
    assert hit.path == "src/app.py"
    assert hit.start_line == 15
    assert hit.start_col == 5
    assert hit.end_line == 15
    assert hit.end_col == 25
    assert "eval" in hit.message
    assert hit.severity == "ERROR"
    assert len(hit.cwe) == 1
    assert "CWE-94" in hit.cwe[0]
    assert len(hit.owasp) == 1
    assert hit.owasp[0] == "A03:2021 Injection"
    assert len(hit.references) == 1
    assert hit.references[0].startswith("https://")


def test_parse_three_results_preserves_order() -> None:
    hits = parse_semgrep_json(_THREE_RESULTS_JSON)
    assert len(hits) == 3
    assert hits[0].check_id.endswith("debug-enabled")
    assert hits[1].check_id.endswith("useless-print")
    assert hits[2].check_id.endswith("direct-response-write")


def test_parse_ignores_non_dict_result_entries() -> None:
    payload = json.dumps(
        {
            "results": [
                {
                    "check_id": "valid",
                    "path": "x.py",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 1},
                    "extra": {"message": "ok", "severity": "INFO"},
                },
                "not a dict",
                42,
                None,
                {
                    "check_id": "also-valid",
                    "path": "y.py",
                    "start": {"line": 2, "col": 2},
                    "end": {"line": 2, "col": 2},
                    "extra": {"message": "ok", "severity": "INFO"},
                },
            ]
        }
    )
    hits = parse_semgrep_json(payload)
    assert len(hits) == 2
    assert hits[0].check_id == "valid"
    assert hits[1].check_id == "also-valid"


def test_parse_coerces_metadata_string_values_to_lists() -> None:
    """Semgrep sometimes emits metadata as a string instead of a list."""
    hits = parse_semgrep_json(_THREE_RESULTS_JSON)
    # Result 3 has owasp + references as strings, not lists.
    third = hits[2]
    assert third.owasp == ["A03:2021 Injection"]
    assert third.references == ["https://owasp.org/www-community/attacks/xss/"]


def test_parse_handles_missing_start_end_gracefully() -> None:
    payload = json.dumps(
        {
            "results": [
                {
                    "check_id": "x",
                    "path": "y.py",
                    "extra": {"message": "m", "severity": "INFO"},
                }
            ]
        }
    )
    hits = parse_semgrep_json(payload)
    assert len(hits) == 1
    assert hits[0].start_line == 0
    assert hits[0].start_col == 0


def test_parse_handles_missing_extra_gracefully() -> None:
    payload = json.dumps(
        {
            "results": [
                {
                    "check_id": "x",
                    "path": "y.py",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 1},
                }
            ]
        }
    )
    hits = parse_semgrep_json(payload)
    assert len(hits) == 1
    assert hits[0].message == ""
    assert hits[0].severity == ""


# --- Severity mapping --------------------------------------------------


def test_severity_mapping_error_is_high() -> None:
    assert _semgrep_severity_to_unified("ERROR") == Severity.HIGH


def test_severity_mapping_warning_is_medium() -> None:
    assert _semgrep_severity_to_unified("WARNING") == Severity.MEDIUM


def test_severity_mapping_info_is_low() -> None:
    assert _semgrep_severity_to_unified("INFO") == Severity.LOW


def test_severity_mapping_unknown_falls_back_to_medium() -> None:
    """Unknown severities get MEDIUM so they still surface."""
    assert _semgrep_severity_to_unified("") == Severity.MEDIUM
    assert _semgrep_severity_to_unified("WEIRD") == Severity.MEDIUM
    assert _semgrep_severity_to_unified("debug") == Severity.MEDIUM  # lowercase input


def test_severity_mapping_is_case_insensitive() -> None:
    assert _semgrep_severity_to_unified("error") == Severity.HIGH
    assert _semgrep_severity_to_unified("Warning") == Severity.MEDIUM
    assert _semgrep_severity_to_unified("info") == Severity.LOW


# --- _raw_to_hit edges -------------------------------------------------


def test_raw_to_hit_defaults_when_fields_missing() -> None:
    raw = {"check_id": "x"}
    hit = _raw_to_hit(raw)
    assert hit is not None
    assert hit.check_id == "x"
    assert hit.path == ""
    assert hit.start_line == 0
    assert hit.cwe == []
    assert hit.owasp == []
    assert hit.references == []


# --- SemgrepRunner.is_available ----------------------------------------


def test_is_available_true_when_binary_on_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/semgrep")
    runner = SemgrepRunner(tmp_path)
    assert runner.is_available() is True


def test_is_available_false_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = SemgrepRunner(tmp_path)
    assert runner.is_available() is False


# --- SemgrepRunner.scan end-to-end (mocked subprocess) -----------------


class _MockProcess:
    """Minimal async subprocess stand-in for semgrep.

    Unlike gitleaks (which writes to a file), semgrep writes JSON to
    stdout. Our mock captures the command line so tests can verify
    the flags and also returns a canned stdout payload.
    """

    def __init__(self, stdout_text: str, stderr_text: str = "", returncode: int = 0) -> None:
        self._stdout = stdout_text.encode("utf-8")
        self._stderr = stderr_text.encode("utf-8")
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_scan_returns_empty_when_no_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    runner = SemgrepRunner(tmp_path)
    assert await runner.scan([], run_id="r1") == []


@pytest.mark.asyncio
async def test_scan_returns_empty_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = SemgrepRunner(tmp_path)
    source = tmp_path / "src.py"
    source.write_text("print('hi')\n")
    assert await runner.scan([source], run_id="r1") == []


@pytest.mark.asyncio
async def test_scan_parses_single_finding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("eval('x')\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess(_ONE_RESULT_JSON)

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == FindingKind.SAST
    assert f.severity == Severity.HIGH  # ERROR -> HIGH
    assert str(f.file) == "src/app.py"
    assert f.line == 15
    assert f.col == 5
    assert "eval" in f.message.lower()
    assert f.rule_id is not None
    assert f.rule_id.startswith("semgrep::")
    assert "exec-detected" in f.rule_id
    assert f.doc_link is not None
    assert f.doc_link.startswith("https://")
    assert f.claude_hint is not None
    assert "OWASP" in f.claude_hint


@pytest.mark.asyncio
async def test_scan_parses_three_findings_with_mixed_severity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess(_THREE_RESULTS_JSON)

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")

    assert len(findings) == 3
    severities = [f.severity for f in findings]
    assert Severity.MEDIUM in severities  # WARNING
    assert Severity.LOW in severities  # INFO
    assert Severity.HIGH in severities  # ERROR


@pytest.mark.asyncio
async def test_scan_returns_empty_on_empty_stdout(tmp_path: Path, monkeypatch) -> None:
    """Zero findings: semgrep writes an empty JSON object or no output at all."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess("")

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")
    assert findings == []


@pytest.mark.asyncio
async def test_scan_returns_empty_on_nonzero_exit_with_empty_stdout(
    tmp_path: Path, monkeypatch
) -> None:
    """Semgrep crashed: log warning and return empty, do not break the hot loop."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess("", stderr_text="config not found: p/default", returncode=2)

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")
    assert findings == []


@pytest.mark.asyncio
async def test_scan_parses_output_even_on_nonzero_exit(tmp_path: Path, monkeypatch) -> None:
    """Some semgrep configurations exit nonzero when findings are present.

    We trust the JSON on stdout regardless of the exit code.
    """
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess(_ONE_RESULT_JSON, returncode=1)

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")
    assert len(findings) == 1


@pytest.mark.asyncio
async def test_scan_uses_default_ruleset(tmp_path: Path, monkeypatch) -> None:
    """The subprocess command line should include `--config p/default`."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    captured: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured[:] = list(args)
        return _MockProcess("")

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        await runner.scan([source], run_id="r1")

    joined = " ".join(captured)
    assert "--config" in joined
    assert DEFAULT_RULESET in joined
    assert "--json" in joined
    assert "--quiet" in joined
    assert "--no-git-ignore" in joined


@pytest.mark.asyncio
async def test_scan_custom_ruleset_overrides_default(tmp_path: Path, monkeypatch) -> None:
    """Constructor `ruleset` parameter overrides the default."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n")

    captured: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured[:] = list(args)
        return _MockProcess("")

    runner = SemgrepRunner(tmp_path, ruleset="p/owasp-top-ten")
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        await runner.scan([source], run_id="r1")

    assert "p/owasp-top-ten" in " ".join(captured)
    assert DEFAULT_RULESET not in " ".join(captured)


@pytest.mark.asyncio
async def test_scan_passes_all_files_in_one_invocation(tmp_path: Path, monkeypatch) -> None:
    """Semgrep is batch-oriented; one subprocess call handles multiple files."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/semgrep")
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    f2 = tmp_path / "b.py"
    f2.write_text("y = 2\n")

    call_count = {"n": 0}
    captured: list[str] = []

    async def fake_exec(*args, **kwargs):
        call_count["n"] += 1
        captured[:] = list(args)
        return _MockProcess("")

    runner = SemgrepRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        await runner.scan([f1, f2], run_id="r1")

    assert call_count["n"] == 1  # batch invocation, not per-file
    joined = " ".join(captured)
    assert str(f1.resolve()) in joined
    assert str(f2.resolve()) in joined


def test_default_ruleset_is_p_default() -> None:
    """Sanity check on the exported default ruleset constant."""
    assert DEFAULT_RULESET == "p/default"
