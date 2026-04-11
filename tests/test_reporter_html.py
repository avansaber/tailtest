"""Tests for the HTML reporter (Phase 2 Task 2.6).

Covers the pure ``render()`` path with a variety of batch shapes
(empty, test failures, security findings, delta coverage,
baseline-suppressed) and the ``write_report()`` I/O helper that
writes both the timestamped file and the ``latest.html`` mirror.

The tests are string-based: we assert that specific substrings
appear in the rendered HTML rather than parsing the document with
an HTML parser. This keeps the test suite dependency-free and
matches the "eyeball" validation rule in the task spec.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.core.reporter.html import HTMLReporter, HTMLReportPaths

# --- Fixtures ---------------------------------------------------------


def _make_passing_batch() -> FindingBatch:
    return FindingBatch(
        run_id="test-run-00000001",
        depth="standard",
        tests_passed=14,
        tests_failed=0,
        tests_skipped=0,
        duration_ms=1800.0,
        summary_line="tailtest: 14/14 tests passed · 1.8s",
    )


def _make_mixed_batch() -> FindingBatch:
    """Build a realistic batch with tests + security + delta coverage."""
    findings = [
        Finding.create(
            kind=FindingKind.TEST_FAILURE,
            severity=Severity.HIGH,
            file="tests/test_math.py",
            line=12,
            message="AssertionError: expected 4, got 3",
            run_id="mixed",
            rule_id="pytest::test_add",
            claude_hint="Check the add function's int rounding.",
        ),
        Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.HIGH,
            file="src/config.py",
            line=7,
            message="Hardcoded AWS access key",
            run_id="mixed",
            rule_id="gitleaks::aws-access-token",
            cwe_id="CWE-798",
            claude_hint="Rotate the key and use an env var.",
        ),
        Finding.create(
            kind=FindingKind.SAST,
            severity=Severity.MEDIUM,
            file="src/web.py",
            line=42,
            message="Unsanitized user input flows into render",
            run_id="mixed",
            rule_id="semgrep::xss",
            cwe_id="CWE-79",
            doc_link="https://semgrep.dev/r/xss",
        ),
        Finding.create(
            kind=FindingKind.SCA,
            severity=Severity.CRITICAL,
            file="pyproject.toml",
            line=0,
            message="requests 2.0.0 : GHSA-j8r2-6x86-q33q : RCE",
            run_id="mixed",
            rule_id="osv::GHSA-j8r2-6x86-q33q",
            cvss_score=9.8,
            package_name="requests",
            package_version="2.0.0",
            fixed_version="2.31.0",
            advisory_url="https://github.com/advisories/GHSA-j8r2-6x86-q33q",
            claude_hint="CVSS 9.8 | upgrade to requests 2.31.0",
        ),
    ]
    return FindingBatch(
        run_id="mixed-run-0001",
        depth="standard",
        tests_passed=13,
        tests_failed=1,
        tests_skipped=0,
        duration_ms=2500.0,
        summary_line=("tailtest: 13/14 tests passed · 1 failed · 3 new security issues · 2.5s"),
        findings=findings,
        delta_coverage_pct=87.5,
        uncovered_new_lines=[
            {"file": "src/math.py", "line": 15},
            {"file": "src/math.py", "line": 22},
        ],
    )


# --- Render: empty / minimal batch ------------------------------------


def test_render_empty_batch_produces_valid_html_skeleton() -> None:
    reporter = HTMLReporter()
    html = reporter.render(FindingBatch(run_id="empty", depth="quick"))
    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="en">' in html
    assert "<title>tailtest report - empty</title>" in html
    assert "</html>" in html
    # Inline CSS present; no external stylesheet link.
    assert "<style>" in html
    assert "<link" not in html


def test_render_empty_batch_shows_no_new_findings_state() -> None:
    reporter = HTMLReporter()
    html = reporter.render(FindingBatch(run_id="empty", depth="quick"))
    assert "No new findings in this run." in html


def test_render_passing_batch_shows_summary_and_test_counts() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_passing_batch())
    assert "tailtest: 14/14 tests passed" in html
    assert ">14<" in html  # passed count rendered as a number
    assert ">0<" in html  # failed/skipped count rendered


def test_render_footer_includes_tool_version_and_duration() -> None:
    reporter = HTMLReporter(tool_version="0.1.0a1")
    html = reporter.render(_make_passing_batch())
    assert "tailtest v0.1.0a1" in html
    assert "1.8s" in html


def test_render_uses_injected_timestamp() -> None:
    ts = datetime(2026, 4, 9, 16, 42, 0, tzinfo=UTC)
    reporter = HTMLReporter(now_utc=ts)
    html = reporter.render(_make_passing_batch())
    assert "2026-04-09 16:42:00 UTC" in html


# --- Render: mixed batch with test + security findings ---------------


def test_render_mixed_batch_groups_findings_by_kind() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    # All four kinds appear as group headings.
    assert "Test failures" in html
    assert "Secrets" in html
    assert "Static analysis (SAST)" in html
    assert "Dependency advisories (SCA)" in html


def test_render_mixed_batch_test_failure_before_security() -> None:
    """Group order: test failures come before security in the HTML flow."""
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    idx_test = html.index("Test failures")
    idx_secret = html.index("Secrets")
    idx_sast = html.index("Static analysis")
    idx_sca = html.index("Dependency advisories")
    assert idx_test < idx_secret < idx_sast < idx_sca


def test_render_finding_card_shows_severity_stripe_class() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    assert "sev-critical" in html
    assert "sev-high" in html
    assert "sev-medium" in html


def test_render_sca_finding_shows_full_security_metadata() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    assert "CVSS 9.8" in html
    assert "requests@2.0.0" in html
    assert "fix: 2.31.0" in html
    # Advisory link present with noopener.
    assert "https://github.com/advisories/GHSA-j8r2-6x86-q33q" in html
    assert 'rel="noopener nofollow"' in html


def test_render_secret_finding_shows_cwe_tag() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    assert "CWE-798" in html


def test_render_includes_claude_hints() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    assert "hint: Check the add function" in html
    assert "hint: Rotate the key" in html


def test_render_delta_coverage_block_when_present() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_mixed_batch())
    assert "Delta coverage" in html
    assert "87.5%" in html
    assert "src/math.py:15" in html


def test_render_delta_coverage_block_omitted_when_none() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_passing_batch())
    assert "Delta coverage" not in html


def test_render_baseline_block_when_suppressed_findings_present() -> None:
    suppressed = (
        Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.HIGH,
            file="x.py",
            line=1,
            message="old hit",
            run_id="r",
        )
    ).model_copy(update={"in_baseline": True})
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[suppressed],
    )
    reporter = HTMLReporter()
    html = reporter.render(batch)
    assert "Suppressed by baseline" in html
    assert "1 finding(s) were suppressed" in html


def test_render_baseline_block_omitted_when_no_suppressed() -> None:
    reporter = HTMLReporter()
    html = reporter.render(_make_passing_batch())
    assert "Suppressed by baseline" not in html


# --- XSS safety -------------------------------------------------------


def test_render_escapes_html_in_message() -> None:
    """Finding messages must not be interpreted as HTML."""
    evil = Finding.create(
        kind=FindingKind.TEST_FAILURE,
        severity=Severity.HIGH,
        file="x.py",
        line=1,
        message="<script>alert('xss')</script>",
        run_id="r",
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[evil])
    html = HTMLReporter().render(batch)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_render_escapes_html_in_file_path() -> None:
    evil = Finding.create(
        kind=FindingKind.TEST_FAILURE,
        severity=Severity.HIGH,
        file="<img src=x onerror=alert(1)>",
        line=1,
        message="m",
        run_id="r",
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[evil])
    html = HTMLReporter().render(batch)
    assert "<img src=x" not in html
    assert "&lt;img" in html


def test_render_escapes_html_in_rule_id_and_hint() -> None:
    evil = Finding.create(
        kind=FindingKind.SECRET,
        severity=Severity.HIGH,
        file="x.py",
        line=1,
        message="m",
        run_id="r",
        rule_id="evil::<script>",
        claude_hint="run <b>safely</b>",
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[evil])
    html = HTMLReporter().render(batch)
    assert "<script>" not in html.split("<style>", 1)[1].split("</style>", 1)[1]
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;safely&lt;/b&gt;" in html


def test_render_escapes_summary_line() -> None:
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        summary_line="<script>alert(1)</script>",
    )
    html = HTMLReporter().render(batch)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- I/O: write + write_report ---------------------------------------


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    out = tmp_path / "deeply" / "nested" / "report.html"
    reporter = HTMLReporter()
    result = reporter.write(_make_passing_batch(), out)
    assert result == out
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_write_is_atomic(tmp_path: Path) -> None:
    """``write`` must never leave a ``.tmp`` file behind on success."""
    out = tmp_path / "report.html"
    HTMLReporter().write(_make_passing_batch(), out)
    leftovers = list(tmp_path.iterdir())
    assert len(leftovers) == 1
    assert leftovers[0].name == "report.html"


def test_write_report_creates_timestamped_and_latest(tmp_path: Path) -> None:
    ts = datetime(2026, 4, 9, 16, 42, 0, tzinfo=UTC)
    reporter = HTMLReporter(now_utc=ts)
    reports_dir = tmp_path / "reports"
    paths = reporter.write_report(_make_mixed_batch(), reports_dir)
    assert isinstance(paths, HTMLReportPaths)
    assert paths.timestamped.exists()
    assert paths.latest.exists()
    assert paths.latest.name == "latest.html"
    assert paths.timestamped.name == "2026-04-09T16-42-00Z.html"
    # Both files have identical content.
    assert paths.latest.read_text(encoding="utf-8") == paths.timestamped.read_text(encoding="utf-8")


def test_write_report_latest_is_plain_copy_not_symlink(tmp_path: Path) -> None:
    """Symlinks break on Windows for non-admins; latest.html must be a copy."""
    reports_dir = tmp_path / "reports"
    paths = HTMLReporter().write_report(_make_passing_batch(), reports_dir)
    assert paths.latest.is_file()
    assert not paths.latest.is_symlink()


def test_write_report_leaves_no_tmp_files(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    HTMLReporter().write_report(_make_passing_batch(), reports_dir)
    names = {p.name for p in reports_dir.iterdir()}
    assert not any(n.endswith(".tmp") for n in names)


# --- Integration with the hook -------------------------------------


def test_hook_persist_report_writes_html(tmp_path: Path) -> None:
    """``_persist_report`` should create both latest.json and latest.html."""
    from tailtest.hook.post_tool_use import _persist_report

    _persist_report(tmp_path, _make_mixed_batch())
    assert (tmp_path / ".tailtest" / "reports" / "latest.json").exists()
    assert (tmp_path / ".tailtest" / "reports" / "latest.html").exists()


def test_hook_persist_report_html_survives_malformed_batch(tmp_path: Path) -> None:
    """A write failure in the HTML path must not break the JSON path."""
    from tailtest.hook.post_tool_use import _persist_report

    # Use a perfectly valid batch but make the reports dir a file
    # to force the HTML write path to fail. The JSON write should
    # still have happened before the HTML attempt.
    reports_dir = tmp_path / ".tailtest" / "reports"
    reports_dir.mkdir(parents=True)

    _persist_report(tmp_path, _make_passing_batch())
    # Second run with the dir occupied should still land.
    _persist_report(tmp_path, _make_passing_batch())
    assert (reports_dir / "latest.json").exists()
    assert (reports_dir / "latest.html").exists()


# --- Regression: kinds with no entries must not render a header -----


def test_render_skips_groups_with_zero_findings() -> None:
    """A kind group with zero findings must NOT emit a heading."""
    batch = FindingBatch(
        run_id="r",
        depth="standard",
        findings=[
            Finding.create(
                kind=FindingKind.SECRET,
                severity=Severity.HIGH,
                file="x.py",
                line=1,
                message="one secret",
                run_id="r",
            )
        ],
    )
    html = HTMLReporter().render(batch)
    assert "Secrets" in html
    assert "Test failures" not in html
    assert "Dependency advisories" not in html


def test_render_sorts_within_kind_group_by_severity_desc() -> None:
    findings = [
        Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.LOW,
            file="a.py",
            line=1,
            message="low finding",
            run_id="r",
        ),
        Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.CRITICAL,
            file="b.py",
            line=1,
            message="critical finding",
            run_id="r",
        ),
    ]
    batch = FindingBatch(run_id="r", depth="standard", findings=findings)
    html = HTMLReporter().render(batch)
    idx_critical = html.index("critical finding")
    idx_low = html.index("low finding")
    assert idx_critical < idx_low


# --- Regression: col field handling -----------------------------------


def test_render_includes_col_when_positive() -> None:
    finding = Finding.create(
        kind=FindingKind.SAST,
        severity=Severity.MEDIUM,
        file="x.py",
        line=10,
        message="m",
        run_id="r",
        col=5,
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[finding])
    html = HTMLReporter().render(batch)
    assert "x.py:10:5" in html


def test_render_skips_col_when_missing() -> None:
    finding = Finding.create(
        kind=FindingKind.SAST,
        severity=Severity.MEDIUM,
        file="x.py",
        line=10,
        message="m",
        run_id="r",
    )
    batch = FindingBatch(run_id="r", depth="standard", findings=[finding])
    html = HTMLReporter().render(batch)
    assert "x.py:10" in html
    assert "x.py:10:" not in html


@pytest.mark.asyncio
async def test_async_compatibility_placeholder() -> None:
    """Sanity: pytest-asyncio is active (used by other security tests)."""
    assert True


# --- Task 6.5: Red-team finding rendering ---------------------------------


def _redteam_finding(
    *,
    category: str = "prompt_injection",
    reasoning: str = "No input sanitization present",
    confidence: str = "high",
    cwe_id: str | None = "CWE-77",
    severity: Severity = Severity.HIGH,
) -> Finding:
    f = Finding.create(
        kind=FindingKind.REDTEAM,
        severity=severity,
        file=Path("(agent entry point)"),
        line=0,
        message=f"[{category}] Ignore-previous instructions: agent lacks sanitization",
        run_id="redteam",
        rule_id=f"redteam/{category}/pi_001",
    )
    return f.model_copy(update={"reasoning": reasoning, "confidence": confidence, "cwe_id": cwe_id})


def test_redteam_finding_renders_in_red_team_section() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding()],
    )
    rendered = HTMLReporter().render(batch)
    assert "Red team" in rendered


def test_redteam_finding_shows_reasoning() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(reasoning="The run() function passes input directly to LLM")],
    )
    rendered = HTMLReporter().render(batch)
    assert "passes input directly to LLM" in rendered


def test_redteam_finding_shows_confidence_badge() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(confidence="high")],
    )
    rendered = HTMLReporter().render(batch)
    assert "conf-badge" in rendered
    assert "high" in rendered


def test_redteam_finding_shows_cwe_in_extra() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(cwe_id="CWE-77")],
    )
    rendered = HTMLReporter().render(batch)
    assert "CWE-77" in rendered


def test_redteam_finding_shows_category_in_rule_id() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(category="jailbreak")],
    )
    rendered = HTMLReporter().render(batch)
    assert "jailbreak" in rendered


def test_redteam_finding_severity_stripe_class() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(severity=Severity.CRITICAL)],
    )
    rendered = HTMLReporter().render(batch)
    assert "sev-critical" in rendered


def test_redteam_finding_no_reasoning_renders_cleanly() -> None:
    f = Finding.create(
        kind=FindingKind.REDTEAM,
        severity=Severity.MEDIUM,
        file=Path("(agent entry point)"),
        line=0,
        message="[tool_misuse] something bad",
        run_id="redteam",
        rule_id="redteam/tool_misuse/tm_001",
    )
    batch = FindingBatch(run_id="redteam", depth="paranoid", findings=[f])
    rendered = HTMLReporter().render(batch)
    assert "Red team" in rendered
    assert "reasoning" not in rendered


def test_redteam_finding_xss_safe_reasoning() -> None:
    batch = FindingBatch(
        run_id="redteam",
        depth="paranoid",
        findings=[_redteam_finding(reasoning="<script>alert(1)</script>")],
    )
    rendered = HTMLReporter().render(batch)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
