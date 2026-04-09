"""Tests for the unified `Finding` schema (Phase 1 Task 1.1)."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest
from pydantic import ValidationError

from tailtest.core.findings import (
    SCHEMA_VERSION,
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
    compute_finding_id,
)

# --- Enums ----------------------------------------------------------------


def test_finding_kind_has_expected_values() -> None:
    """All 9 finding kinds are defined and accessible."""
    expected = {
        "test_failure",
        "lint",
        "secret",
        "sast",
        "sca",
        "coverage_gap",
        "ai_surface",
        "validator",
        "redteam",
    }
    assert {k.value for k in FindingKind} == expected


def test_severity_ranks_are_monotonic() -> None:
    """INFO < LOW < MEDIUM < HIGH < CRITICAL."""
    assert Severity.INFO.rank < Severity.LOW.rank
    assert Severity.LOW.rank < Severity.MEDIUM.rank
    assert Severity.MEDIUM.rank < Severity.HIGH.rank
    assert Severity.HIGH.rank < Severity.CRITICAL.rank


# --- ID stability ---------------------------------------------------------


def test_compute_finding_id_is_deterministic() -> None:
    """Same inputs → same ID, always."""
    a = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        42,
        "pytest.assertion",
        "assert a == b where a=1, b=2",
    )
    b = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        42,
        "pytest.assertion",
        "assert a == b where a=1, b=2",
    )
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_compute_finding_id_ignores_timestamp_drift() -> None:
    """A message with an ISO timestamp should hash the same before/after."""
    a = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "test failed at 2026-04-09T08:15:30Z",
    )
    b = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "test failed at 2026-04-10T14:22:11Z",
    )
    assert a == b, "ISO timestamps must not change the ID"


def test_compute_finding_id_ignores_float_drift() -> None:
    """A message with a duration float should hash the same regardless of duration."""
    a = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "test took 1.234s",
    )
    b = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "test took 2.789s",
    )
    assert a == b, "Duration floats must not change the ID"


def test_compute_finding_id_ignores_hex_addresses() -> None:
    """Hex addresses in stack traces should normalize out."""
    a = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "segfault at 0xdeadbeef",
    )
    b = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "src/foo.py",
        10,
        None,
        "segfault at 0xcafef00d",
    )
    assert a == b, "Hex addresses must not change the ID"


def test_compute_finding_id_distinguishes_real_content_changes() -> None:
    """Changing the rule_id or file path MUST change the ID."""
    base = compute_finding_id(
        FindingKind.SAST,
        "src/foo.py",
        42,
        "semgrep.eval-injection",
        "eval of user input",
    )
    different_rule = compute_finding_id(
        FindingKind.SAST,
        "src/foo.py",
        42,
        "semgrep.xss",
        "eval of user input",
    )
    different_file = compute_finding_id(
        FindingKind.SAST,
        "src/bar.py",
        42,
        "semgrep.eval-injection",
        "eval of user input",
    )
    different_line = compute_finding_id(
        FindingKind.SAST,
        "src/foo.py",
        100,
        "semgrep.eval-injection",
        "eval of user input",
    )
    assert base != different_rule
    assert base != different_file
    assert base != different_line


# --- Finding construction + round-trip -----------------------------------


def test_finding_create_auto_computes_id() -> None:
    """Finding.create() should produce the same id as compute_finding_id()."""
    finding = Finding.create(
        kind=FindingKind.TEST_FAILURE,
        severity=Severity.HIGH,
        file="tests/test_example.py",
        line=15,
        message="expected 3, got 7",
        run_id="run-001",
        rule_id="pytest.assertion",
    )
    expected_id = compute_finding_id(
        FindingKind.TEST_FAILURE,
        "tests/test_example.py",
        15,
        "pytest.assertion",
        "expected 3, got 7",
    )
    assert finding.id == expected_id
    assert finding.kind == FindingKind.TEST_FAILURE
    assert finding.severity == Severity.HIGH
    assert finding.file == Path("tests/test_example.py")
    assert finding.line == 15


def test_finding_roundtrip_json() -> None:
    """Serialize to JSON and parse back — all fields preserved."""
    finding = Finding.create(
        kind=FindingKind.SECRET,
        severity=Severity.CRITICAL,
        file="src/config.py",
        line=7,
        message="hardcoded API key: sk-***",
        run_id="run-002",
        rule_id="gitleaks.aws-access-key",
        cwe_id="CWE-798",
        claude_hint="Move the key to an environment variable.",
    )
    json_str = finding.model_dump_json()
    restored = Finding.model_validate_json(json_str)
    assert restored == finding


def test_finding_roundtrip_all_security_fields() -> None:
    """All eight Phase 2 security metadata fields survive a JSON roundtrip.

    Covers every optional field added for Tasks 2.1 through 2.3:
    cwe_id, cvss_score, epss_score, kev_listed, package_name,
    package_version, fixed_version, advisory_url. Doubles as a
    regression guard against accidental schema breakage.
    """
    finding = Finding.create(
        kind=FindingKind.SCA,
        severity=Severity.HIGH,
        file="pyproject.toml",
        line=0,
        message="requests 2.0.0 : GHSA-j8r2-6x86-q33q : vuln summary",
        run_id="run-sec",
        rule_id="osv::GHSA-j8r2-6x86-q33q",
        cwe_id="CWE-601",
        cvss_score=8.1,
        epss_score=0.42,
        kev_listed=True,
        package_name="requests",
        package_version="2.0.0",
        fixed_version="2.31.0",
        advisory_url="https://github.com/advisories/GHSA-j8r2-6x86-q33q",
        claude_hint="CVSS 8.1 | GHSA-j8r2-6x86-q33q | upgrade to 2.31.0",
    )
    assert finding.cwe_id == "CWE-601"
    assert finding.cvss_score == 8.1
    assert finding.epss_score == 0.42
    assert finding.kev_listed is True
    assert finding.package_name == "requests"
    assert finding.package_version == "2.0.0"
    assert finding.fixed_version == "2.31.0"
    assert finding.advisory_url == "https://github.com/advisories/GHSA-j8r2-6x86-q33q"

    # Roundtrip: serialize to JSON, deserialize, compare full equality.
    json_str = finding.model_dump_json()
    restored = Finding.model_validate_json(json_str)
    assert restored == finding
    assert restored.cvss_score == 8.1
    assert restored.fixed_version == "2.31.0"
    assert restored.kev_listed is True


def test_finding_security_fields_default_to_none() -> None:
    """Findings without security metadata leave every security field as None.

    Non-security findings (test failures, lint warnings, coverage
    gaps) MUST not accidentally inherit security metadata. This is
    the backwards-compat guard for the Phase 1 finding shape.
    """
    finding = Finding.create(
        kind=FindingKind.TEST_FAILURE,
        severity=Severity.HIGH,
        file="tests/test_example.py",
        line=42,
        message="AssertionError: expected 3, got 7",
        run_id="run-003",
    )
    assert finding.cwe_id is None
    assert finding.cvss_score is None
    assert finding.epss_score is None
    assert finding.kev_listed is None
    assert finding.package_name is None
    assert finding.package_version is None
    assert finding.fixed_version is None
    assert finding.advisory_url is None


def test_finding_extra_fields_rejected() -> None:
    """Pydantic extra='forbid' — unknown fields must error."""
    with pytest.raises(ValidationError):
        Finding(
            id="abc123def456abcd",
            kind=FindingKind.LINT,
            severity=Severity.LOW,
            file=Path("x"),
            line=1,
            message="m",
            run_id="r",
            some_bogus_field=True,  # type: ignore[call-arg]
        )


def test_finding_schema_version_is_1() -> None:
    """Phase 1 schema is version 1. Bumping this field requires an ADR."""
    assert SCHEMA_VERSION == 1
    finding = Finding.create(
        kind=FindingKind.LINT,
        severity=Severity.INFO,
        file="x",
        line=1,
        message="m",
        run_id="r",
    )
    assert finding.schema_version == 1


def test_finding_timestamp_is_utc() -> None:
    """Default timestamp should be timezone-aware UTC."""
    finding = Finding.create(
        kind=FindingKind.LINT,
        severity=Severity.INFO,
        file="x",
        line=1,
        message="m",
        run_id="r",
    )
    assert finding.timestamp.tzinfo is UTC


# --- FindingBatch --------------------------------------------------------


def _make_finding(
    run_id: str, severity: Severity = Severity.LOW, *, in_baseline: bool = False
) -> Finding:
    f = Finding.create(
        kind=FindingKind.LINT,
        severity=severity,
        file="src/x.py",
        line=1,
        message=f"msg-{severity.value}-{in_baseline}",
        run_id=run_id,
    )
    return f.model_copy(update={"in_baseline": in_baseline})


def test_finding_batch_counts_by_severity() -> None:
    """Counts should match the per-severity tally."""
    batch = FindingBatch(
        run_id="run-003",
        depth="standard",
        findings=[
            _make_finding("run-003", Severity.LOW),
            _make_finding("run-003", Severity.LOW),
            _make_finding("run-003", Severity.MEDIUM),
            _make_finding("run-003", Severity.HIGH),
        ],
    )
    assert batch.counts == {"low": 2, "medium": 1, "high": 1}


def test_finding_batch_new_findings_excludes_baseline() -> None:
    """new_findings should drop findings with in_baseline=True."""
    batch = FindingBatch(
        run_id="run-004",
        depth="standard",
        findings=[
            _make_finding("run-004", Severity.LOW, in_baseline=True),
            _make_finding("run-004", Severity.MEDIUM, in_baseline=False),
        ],
    )
    new = batch.new_findings
    assert len(new) == 1
    assert new[0].severity == Severity.MEDIUM


def test_finding_batch_with_baseline_applied() -> None:
    """Applying a baseline set should flip in_baseline for matching IDs."""
    f1 = _make_finding("run-005", Severity.MEDIUM)
    f2 = _make_finding("run-005", Severity.HIGH)
    batch = FindingBatch(run_id="run-005", depth="quick", findings=[f1, f2])

    new_batch = batch.with_baseline_applied({f1.id})
    assert new_batch.findings[0].in_baseline is True
    assert new_batch.findings[1].in_baseline is False
    # Original batch is unchanged (immutable semantics for this method)
    assert batch.findings[0].in_baseline is False


def test_finding_batch_default_values() -> None:
    """Empty batch defaults are sensible."""
    batch = FindingBatch(run_id="run-006", depth="off")
    assert batch.findings == []
    assert batch.tests_passed == 0
    assert batch.tests_failed == 0
    assert batch.duration_ms == 0.0
    assert batch.counts == {}
    assert batch.new_findings == []


def test_finding_batch_schema_version() -> None:
    """Every batch carries the current schema version."""
    batch = FindingBatch(run_id="run-007", depth="standard")
    assert batch.schema_version == 1
