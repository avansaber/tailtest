"""Tests for TerminalReporter (Phase 1 Task 1.8)."""

from __future__ import annotations

from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind, Severity
from tailtest.core.reporter import TerminalReporter


def _make_finding(
    kind: FindingKind = FindingKind.TEST_FAILURE,
    severity: Severity = Severity.HIGH,
    *,
    file: str = "tests/test_foo.py",
    line: int = 42,
    message: str = "assert a == b where a=1, b=2",
    **kwargs: object,
) -> Finding:
    return Finding.create(
        kind=kind,
        severity=severity,
        file=file,
        line=line,
        message=message,
        run_id="run-test",
        rule_id="test.assertion",
        **kwargs,  # type: ignore[arg-type]
    )


# --- Summary line --------------------------------------------------------


def test_summary_line_clean_run() -> None:
    """14/14 tests passed · 1.2s"""
    reporter = TerminalReporter(use_color=False)
    batch = FindingBatch(
        run_id="r1",
        depth="standard",
        tests_passed=14,
        tests_failed=0,
        duration_ms=1200.0,
    )
    out = reporter.format(batch)
    assert "tailtest:" in out
    assert "14/14 tests passed" in out
    assert "1.20s" in out
    assert "[standard]" in out


def test_summary_line_with_failures() -> None:
    """13/14 tests passed · 1 failed"""
    reporter = TerminalReporter(use_color=False)
    failing = _make_finding(kind=FindingKind.TEST_FAILURE, severity=Severity.HIGH)
    batch = FindingBatch(
        run_id="r2",
        depth="standard",
        tests_passed=13,
        tests_failed=1,
        findings=[failing],
        duration_ms=1200.0,
    )
    out = reporter.format(batch)
    assert "13/14 tests passed" in out
    assert "1 failed" in out
    assert str(failing.file) in out


def test_summary_line_mixed_findings() -> None:
    """Summary surfaces both test failures and non-test findings."""
    reporter = TerminalReporter(use_color=False)
    batch = FindingBatch(
        run_id="r3",
        depth="standard",
        tests_passed=10,
        tests_failed=0,
        findings=[
            _make_finding(kind=FindingKind.SAST, severity=Severity.HIGH, line=1),
            _make_finding(kind=FindingKind.SECRET, severity=Severity.CRITICAL, line=2),
        ],
        duration_ms=800.0,
    )
    out = reporter.format(batch)
    assert "10/10 tests passed" in out
    assert "sast" in out
    assert "secret" in out


def test_summary_line_no_tests_no_findings() -> None:
    """When there are no tests or findings, summary says 'clean'."""
    reporter = TerminalReporter(use_color=False)
    batch = FindingBatch(run_id="r4", depth="quick", duration_ms=300.0)
    out = reporter.format(batch)
    assert "clean" in out


# --- Finding detail -------------------------------------------------------


def test_finding_detail_includes_file_line() -> None:
    reporter = TerminalReporter(use_color=False)
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[
            _make_finding(file="src/auth.py", line=99, message="password stored in plaintext")
        ],
        tests_failed=1,
    )
    out = reporter.format(batch)
    assert "src/auth.py:99" in out
    assert "password stored in plaintext" in out


def test_finding_detail_includes_claude_hint_when_present() -> None:
    reporter = TerminalReporter(use_color=False)
    finding = _make_finding(
        message="the short message",
        claude_hint="use bcrypt with cost ≥ 12",
    )
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[finding],
        tests_failed=1,
    )
    out = reporter.format(batch)
    assert "use bcrypt" in out


def test_finding_detail_omits_hint_when_duplicate_of_message() -> None:
    """If claude_hint equals the message, don't repeat it."""
    reporter = TerminalReporter(use_color=False)
    finding = _make_finding(
        message="same text",
        claude_hint="same text",
    )
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[finding],
        tests_failed=1,
    )
    out = reporter.format(batch)
    # "same text" appears exactly once (in the message line, not duplicated)
    assert out.count("same text") == 1


def test_finding_detail_includes_fix_suggestion() -> None:
    reporter = TerminalReporter(use_color=False)
    finding = _make_finding(
        message="eval of user input",
        fix_suggestion="use ast.literal_eval",
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[finding], tests_failed=1)
    out = reporter.format(batch)
    assert "fix: use ast.literal_eval" in out


# --- Sorting and truncation ----------------------------------------------


def test_findings_sorted_by_severity_descending() -> None:
    """Highest severity listed first."""
    reporter = TerminalReporter(use_color=False)
    low = _make_finding(severity=Severity.LOW, file="a.py", line=1, message="lo")
    high = _make_finding(severity=Severity.HIGH, file="b.py", line=1, message="hi")
    critical = _make_finding(severity=Severity.CRITICAL, file="c.py", line=1, message="ur")
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[low, high, critical],
        tests_failed=3,
    )
    out = reporter.format(batch)
    pos_critical = out.index("c.py:1")
    pos_high = out.index("b.py:1")
    pos_low = out.index("a.py:1")
    assert pos_critical < pos_high < pos_low


def test_findings_truncated_beyond_max() -> None:
    """More than max_findings findings → truncation message."""
    reporter = TerminalReporter(use_color=False, max_findings=3)
    findings = [_make_finding(file=f"src/f{i}.py", line=i, message=f"msg {i}") for i in range(10)]
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=findings,
        tests_failed=10,
    )
    out = reporter.format(batch)
    assert "7 more findings" in out
    assert ".tailtest/reports/latest.json" in out


# --- Baseline handling ---------------------------------------------------


def test_baselined_findings_hidden_by_default() -> None:
    """Findings with in_baseline=True are not shown unless show_baseline=True."""
    reporter = TerminalReporter(use_color=False)
    f_new = _make_finding(file="a.py", line=1, message="new finding")
    f_base = _make_finding(file="b.py", line=1, message="baselined").model_copy(
        update={"in_baseline": True}
    )
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[f_new, f_base],
        tests_failed=2,
    )
    out = reporter.format(batch)
    assert "a.py:1" in out
    assert "b.py:1" not in out


def test_baselined_findings_shown_when_requested() -> None:
    reporter = TerminalReporter(use_color=False)
    f_base = _make_finding(file="b.py", line=1).model_copy(update={"in_baseline": True})
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[f_base],
        tests_failed=1,
    )
    out = reporter.format(batch, show_baseline=True)
    assert "b.py:1" in out


# --- Color behavior ------------------------------------------------------


def test_use_color_false_has_no_ansi_codes() -> None:
    reporter = TerminalReporter(use_color=False)
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[_make_finding()],
        tests_failed=1,
    )
    out = reporter.format(batch)
    assert "\x1b[" not in out


def test_use_color_true_has_ansi_codes() -> None:
    reporter = TerminalReporter(use_color=True)
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[_make_finding()],
        tests_failed=1,
    )
    out = reporter.format(batch)
    assert "\x1b[" in out
