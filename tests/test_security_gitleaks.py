"""Tests for GitleaksRunner (Phase 2 Task 2.1).

Pure JSON parsing is tested with canned gitleaks output. The
subprocess path is tested with a mocked ``asyncio.create_subprocess_exec``
so the suite never depends on a real gitleaks binary being on PATH.
One integration-style test uses a `monkeypatch` to simulate the
missing-binary case.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.security.secrets.gitleaks import (
    GitleaksRunner,
    _GitleaksHit,
    _raw_to_hit,
    parse_gitleaks_json,
)

# --- Pure JSON parser ---------------------------------------------------


_ONE_HIT_JSON = json.dumps(
    [
        {
            "RuleID": "aws-access-token",
            "Description": "AWS Access Token",
            "StartLine": 10,
            "EndLine": 10,
            "StartColumn": 5,
            "EndColumn": 30,
            "Match": "<fake-test-placeholder-not-a-real-key>",
            "Secret": "<fake-test-placeholder-not-a-real-key>",
            "File": "/tmp/project/src/config.py",
            "SymlinkFile": "",
            "Commit": "",
            "Entropy": 4.75,
            "Author": "",
            "Email": "",
            "Date": "",
            "Message": "",
            "Tags": [],
            "RuleDescription": "AWS Access Token",
            "Fingerprint": "a1b2c3d4e5f6",
        }
    ]
)

_TWO_HIT_JSON = json.dumps(
    [
        {
            "RuleID": "github-pat",
            "Description": "GitHub Personal Access Token",
            "StartLine": 42,
            "StartColumn": 8,
            "File": "src/secrets.py",
            "Secret": "ghp_xxxxxx",
            "Entropy": 5.1,
            "Fingerprint": "f1",
        },
        {
            "RuleID": "slack-webhook",
            "Description": "Slack Webhook URL",
            "StartLine": 7,
            "StartColumn": 1,
            "File": "src/secrets.py",
            "Secret": "https://hooks.slack.com/services/T00/B00/XYZ",
            "Entropy": 3.2,
            "Fingerprint": "f2",
        },
    ]
)


def test_parse_empty_string_returns_empty_list() -> None:
    assert parse_gitleaks_json("") == []
    assert parse_gitleaks_json("   \n") == []


def test_parse_malformed_json_returns_empty_list() -> None:
    assert parse_gitleaks_json("{not json") == []


def test_parse_null_returns_empty_list() -> None:
    """gitleaks writes `null` when zero findings."""
    assert parse_gitleaks_json("null") == []


def test_parse_non_list_root_returns_empty_list() -> None:
    assert parse_gitleaks_json('{"key": "value"}') == []
    assert parse_gitleaks_json('"just a string"') == []


def test_parse_single_hit_extracts_all_fields() -> None:
    hits = parse_gitleaks_json(_ONE_HIT_JSON)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, _GitleaksHit)
    assert hit.rule_id == "aws-access-token"
    assert hit.description == "AWS Access Token"
    assert hit.file == "/tmp/project/src/config.py"
    assert hit.start_line == 10
    assert hit.start_column == 5
    assert hit.entropy == 4.75
    assert hit.fingerprint == "a1b2c3d4e5f6"


def test_parse_two_hits_preserves_order() -> None:
    hits = parse_gitleaks_json(_TWO_HIT_JSON)
    assert len(hits) == 2
    assert hits[0].rule_id == "github-pat"
    assert hits[1].rule_id == "slack-webhook"


def test_parse_ignores_non_dict_entries() -> None:
    payload = json.dumps(
        [
            {"RuleID": "valid", "Description": "x", "File": "f.py", "StartLine": 1},
            "not a dict",
            42,
            None,
            {"RuleID": "also-valid", "Description": "y", "File": "g.py", "StartLine": 2},
        ]
    )
    hits = parse_gitleaks_json(payload)
    assert len(hits) == 2
    assert hits[0].rule_id == "valid"
    assert hits[1].rule_id == "also-valid"


def test_raw_to_hit_uses_defaults_for_missing_fields() -> None:
    raw = {"RuleID": "x", "Description": "y", "File": "z.py"}
    hit = _raw_to_hit(raw)
    assert hit is not None
    assert hit.rule_id == "x"
    assert hit.start_line == 0
    assert hit.start_column == 0
    assert hit.entropy == 0.0
    assert hit.fingerprint == ""
    assert hit.secret == ""


def test_raw_to_hit_coerces_string_line_number() -> None:
    """Defensive: gitleaks always emits integers, but stringified
    integers should still parse."""
    raw = {"RuleID": "x", "Description": "y", "File": "z", "StartLine": "42"}
    hit = _raw_to_hit(raw)
    assert hit is not None
    assert hit.start_line == 42


# --- GitleaksRunner.is_available ---------------------------------------


def test_is_available_true_when_binary_on_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gitleaks")
    runner = GitleaksRunner(tmp_path)
    assert runner.is_available() is True


def test_is_available_false_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = GitleaksRunner(tmp_path)
    assert runner.is_available() is False


# --- GitleaksRunner.scan end-to-end (mocked subprocess) ----------------


class _MockProcess:
    """Minimal async subprocess stand-in for ``create_subprocess_exec``.

    Unlike PythonRunner's mock, gitleaks writes its report to a file
    (via ``--report-path``) rather than to stdout. So the mock has
    to actually CREATE that file before returning. The test injects
    the report contents via a closure.
    """

    def __init__(self, report_contents: str, report_path: Path) -> None:
        self._report = report_contents
        self._report_path = report_path
        self.returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        # Write the canned JSON to the report path so the runner
        # finds it when it reads the file after the subprocess.
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text(self._report, encoding="utf-8")
        return b"", b""


@pytest.mark.asyncio
async def test_scan_returns_empty_when_no_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    runner = GitleaksRunner(tmp_path)
    result = await runner.scan([], run_id="r1")
    assert result == []


@pytest.mark.asyncio
async def test_scan_returns_empty_when_binary_missing(tmp_path: Path, monkeypatch) -> None:
    """Missing binary is a silent skip, not an error."""
    monkeypatch.setattr("shutil.which", lambda _name: None)
    runner = GitleaksRunner(tmp_path)
    source = tmp_path / "src.py"
    source.write_text("print('hi')\n")
    result = await runner.scan([source], run_id="r1")
    assert result == []


@pytest.mark.asyncio
async def test_scan_parses_single_finding(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    source = tmp_path / "src.py"
    source.write_text("key = 'fake'\n")

    # The runner creates the temp dir internally; we intercept the
    # subprocess call and locate the --report-path argument to
    # figure out where gitleaks would have written the report.
    report_contents = _ONE_HIT_JSON

    async def fake_exec(*args, **kwargs):
        # Find --report-path in the command.
        cmd = list(args)
        report_idx = cmd.index("--report-path") + 1
        report_path = Path(cmd[report_idx])
        return _MockProcess(report_contents, report_path)

    runner = GitleaksRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == FindingKind.SECRET
    assert f.severity == Severity.HIGH
    assert str(f.file) == "/tmp/project/src/config.py"
    assert f.line == 10
    assert f.col == 5
    assert "AWS Access Token" in f.message
    assert f.rule_id == "gitleaks::aws-access-token"
    assert f.claude_hint is not None
    assert "rotate" in f.claude_hint.lower()


@pytest.mark.asyncio
async def test_scan_parses_multiple_findings_per_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    source = tmp_path / "src.py"
    source.write_text("x = 1\n")

    async def fake_exec(*args, **kwargs):
        cmd = list(args)
        report_idx = cmd.index("--report-path") + 1
        return _MockProcess(_TWO_HIT_JSON, Path(cmd[report_idx]))

    runner = GitleaksRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")

    assert len(findings) == 2
    assert findings[0].rule_id == "gitleaks::github-pat"
    assert findings[1].rule_id == "gitleaks::slack-webhook"


@pytest.mark.asyncio
async def test_scan_continues_after_single_file_failure(tmp_path: Path, monkeypatch) -> None:
    """A subprocess error on one file does not abort the whole batch."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    first = tmp_path / "good.py"
    first.write_text("x = 1\n")
    second = tmp_path / "bad.py"
    second.write_text("x = 2\n")

    call_count = {"n": 0}

    async def fake_exec(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First file fails with a subprocess error.
            raise RuntimeError("simulated gitleaks crash")
        # Second file succeeds with one finding.
        cmd = list(args)
        report_idx = cmd.index("--report-path") + 1
        return _MockProcess(_ONE_HIT_JSON, Path(cmd[report_idx]))

    runner = GitleaksRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([first, second], run_id="r1")

    # First scan failed silently, second scan produced 1 finding.
    assert len(findings) == 1
    assert findings[0].rule_id == "gitleaks::aws-access-token"


@pytest.mark.asyncio
async def test_scan_treats_missing_report_file_as_zero_findings(
    tmp_path: Path, monkeypatch
) -> None:
    """If gitleaks does not write the report file, treat it as clean."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    source = tmp_path / "src.py"
    source.write_text("x = 1\n")

    class _NoReportProcess:
        returncode = 0

        async def communicate(self):
            # Deliberately do NOT write the report file.
            return b"", b""

    async def fake_exec(*args, **kwargs):
        return _NoReportProcess()

    runner = GitleaksRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        findings = await runner.scan([source], run_id="r1")
    assert findings == []


# --- Command argument sanity -------------------------------------------


@pytest.mark.asyncio
async def test_scan_uses_expected_gitleaks_flags(tmp_path: Path, monkeypatch) -> None:
    """The subprocess command line should include the flags the
    docstring promises: --source, --no-git, --report-format json,
    --report-path, --no-banner, --exit-code 0."""
    monkeypatch.setattr("shutil.which", lambda _name: "/fake/gitleaks")
    source = tmp_path / "src.py"
    source.write_text("x = 1\n")

    captured_cmd: list[str] = []

    async def fake_exec(*args, **kwargs):
        captured_cmd[:] = list(args)
        cmd = list(args)
        report_idx = cmd.index("--report-path") + 1
        return _MockProcess("[]", Path(cmd[report_idx]))

    runner = GitleaksRunner(tmp_path)
    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        await runner.scan([source], run_id="r1")

    joined = " ".join(captured_cmd)
    assert "--source" in joined
    assert "--no-git" in joined
    assert "--report-format json" in joined
    assert "--report-path" in joined
    assert "--no-banner" in joined
    assert "--exit-code 0" in joined
