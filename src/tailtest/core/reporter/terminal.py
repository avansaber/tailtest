"""TerminalReporter — compact ANSI-colored output for the hot loop (Phase 1 Task 1.8).

Format goals:

- **One-line summary** for clean runs: `tailtest: 14/14 tests passed · 1.2s`
- **One-line summary + compact detail** for runs with findings:
  `tailtest: 13/14 tests passed · 1 failure · 1.2s`
  followed by a 3-6 line block per finding
- **Never more than ~20 lines of output total** — this is what users see in
  Claude Code's additionalContext; every extra line is noise
- **Color when on a TTY**, plain text otherwise — detected via `sys.stdout.isatty()`
- **Baseline findings are suppressed** by default; use `show_baseline=True`
  to see the "debt view"

Output is pure string return — printing is the caller's concern. This
makes the reporter easy to unit test.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from tailtest.core.coverage.delta import DEFAULT_DELTA_COVERAGE_THRESHOLD
from tailtest.core.findings.schema import Finding, FindingBatch, Severity

# --- ANSI helpers ---------------------------------------------------------


@dataclass(frozen=True)
class _Color:
    reset: str = "\x1b[0m"
    bold: str = "\x1b[1m"
    dim: str = "\x1b[2m"
    green: str = "\x1b[32m"
    yellow: str = "\x1b[33m"
    red: str = "\x1b[31m"
    magenta: str = "\x1b[35m"
    cyan: str = "\x1b[36m"
    white: str = "\x1b[37m"


_C = _Color()
_NO_COLOR = _Color(
    reset="",
    bold="",
    dim="",
    green="",
    yellow="",
    red="",
    magenta="",
    cyan="",
    white="",
)


def _severity_color(severity: Severity, c: _Color) -> str:
    """Map a severity to its display color."""
    return {
        Severity.INFO: c.dim,
        Severity.LOW: c.cyan,
        Severity.MEDIUM: c.yellow,
        Severity.HIGH: c.red,
        Severity.CRITICAL: c.red + c.bold,
    }[severity]


def _severity_icon(severity: Severity) -> str:
    """ASCII-only icon for the severity (no emoji — terminal-safe)."""
    return {
        Severity.INFO: "i",
        Severity.LOW: "-",
        Severity.MEDIUM: "!",
        Severity.HIGH: "x",
        Severity.CRITICAL: "X",
    }[severity]


# --- Reporter -------------------------------------------------------------


class TerminalReporter:
    """Formats a FindingBatch into terminal-friendly ANSI text.

    Parameters
    ----------
    use_color:
        If None (default), auto-detects via `sys.stdout.isatty()`. Passing
        True or False forces the behavior (useful for tests and pipes).
    max_findings:
        Maximum number of finding detail blocks to emit. Anything beyond
        is summarized as "... N more" with a pointer to the full report.
        Defaults to 5 (matches the audit's 5-finding truncation rule).
    """

    def __init__(
        self,
        *,
        use_color: bool | None = None,
        max_findings: int = 5,
    ) -> None:
        self._use_color = sys.stdout.isatty() if use_color is None else use_color
        self._c = _C if self._use_color else _NO_COLOR
        self._max_findings = max_findings

    def format(self, batch: FindingBatch, *, show_baseline: bool = False) -> str:
        """Render a batch to a terminal-ready string."""
        lines: list[str] = [self._format_summary(batch)]

        # Delta coverage line, when present (Phase 1 Task 1.8a). Sits
        # between the summary and the finding details so the user sees
        # it without scrolling even in a clean run with no findings.
        coverage_line = self._format_delta_coverage(batch)
        if coverage_line:
            lines.append(coverage_line)

        findings_to_show = batch.findings if show_baseline else batch.new_findings

        if findings_to_show:
            # Sort by severity (highest first), then by file:line for stability
            findings_sorted = sorted(
                findings_to_show,
                key=lambda f: (-f.severity.rank, str(f.file), f.line),
            )

            truncated = findings_sorted[: self._max_findings]
            overflow = len(findings_sorted) - len(truncated)

            for finding in truncated:
                lines.append("")  # blank line between findings for readability
                lines.extend(self._format_finding(finding))

            if overflow > 0:
                lines.append("")
                lines.append(
                    self._c.dim
                    + f"  ... {overflow} more finding{'s' if overflow != 1 else ''} — see .tailtest/reports/latest.json"
                    + self._c.reset
                )

        return "\n".join(lines)

    def _format_summary(self, batch: FindingBatch) -> str:
        """Build the one-line summary at the top."""
        new_findings = batch.new_findings
        has_failures = any(f.severity.rank >= Severity.MEDIUM.rank for f in new_findings)

        # Test results component
        test_total = batch.tests_passed + batch.tests_failed
        if test_total > 0:
            if batch.tests_failed == 0:
                tests_part = (
                    f"{self._c.green}{batch.tests_passed}/{test_total} tests passed{self._c.reset}"
                )
            else:
                tests_part = (
                    f"{self._c.red}{batch.tests_passed}/{test_total} tests passed · "
                    f"{batch.tests_failed} failed{self._c.reset}"
                )
        else:
            tests_part = None

        # Findings component (non-test kinds)
        non_test_new = [f for f in new_findings if f.kind.value != "test_failure"]
        if non_test_new:
            counts: dict[str, int] = {}
            for f in non_test_new:
                key = f.kind.value
                counts[key] = counts.get(key, 0) + 1
            findings_parts = [f"{n} {kind}" for kind, n in counts.items()]
            findings_part = self._c.yellow + " · ".join(findings_parts) + self._c.reset
        else:
            findings_part = None

        # Duration component
        duration_part = f"{batch.duration_ms / 1000.0:.2f}s"

        # Depth tag (small, dim, right-aligned intent)
        depth_part = f"{self._c.dim}[{batch.depth}]{self._c.reset}"

        # Assemble
        components = [
            f"{self._c.bold}tailtest:{self._c.reset}",
        ]
        if tests_part:
            components.append(tests_part)
        if findings_part:
            components.append(findings_part)
        components.append(f"{self._c.dim}· {duration_part}{self._c.reset}")
        components.append(depth_part)

        if not tests_part and not findings_part:
            if has_failures:
                components.insert(1, f"{self._c.red}findings{self._c.reset}")
            else:
                components.insert(1, f"{self._c.green}clean{self._c.reset}")

        return " ".join(components)

    def _format_delta_coverage(self, batch: FindingBatch) -> str | None:
        """Render the delta coverage line, or None when not computed.

        Shows the percentage and a compact hint when the percentage
        is below the default threshold. The line looks like:

            delta coverage: 72.0% (8 of 11 new lines covered)

        When delta coverage is not available (runner did not collect
        it or there were no new lines), this returns None so callers
        can skip the line entirely.
        """
        if batch.delta_coverage_pct is None:
            return None

        c = self._c
        pct = batch.delta_coverage_pct
        uncovered = len(batch.uncovered_new_lines)
        total_new = batch.uncovered_new_lines  # placeholder for count below
        # Total new lines = uncovered + covered. We do not carry the
        # covered count explicitly in FindingBatch, so infer it from
        # the percentage. This is exact when pct was computed from
        # integer ratios.
        if pct >= 100.0:
            total = uncovered  # all new lines covered, uncovered list may be empty
            covered = uncovered
        elif pct <= 0.0:
            total = max(uncovered, 1)
            covered = 0
        else:
            # Reverse the percentage to get the total. total_uncovered / total = 1 - pct/100
            # => total = uncovered / (1 - pct/100). Guard against float rounding.
            ratio = 1.0 - (pct / 100.0)
            total = int(round(uncovered / ratio)) if ratio > 0 else uncovered
            covered = total - uncovered

        if pct >= DEFAULT_DELTA_COVERAGE_THRESHOLD:
            color = c.green
        elif pct >= 50.0:
            color = c.yellow
        else:
            color = c.red
        _ = total_new  # silence unused-variable warning in the fallback branches

        if total == 0:
            return None

        return (
            f"  {color}delta coverage: {pct:.1f}%{c.reset} "
            f"{c.dim}({covered} of {total} new lines covered){c.reset}"
        )

    def _format_finding(self, finding: Finding) -> list[str]:
        """Build the 3-6 line detail block for a single finding."""
        c = self._c
        sev_color = _severity_color(finding.severity, c)
        sev_icon = _severity_icon(finding.severity)

        # Line 1: severity icon + file:line + kind
        header = (
            f"  {sev_color}[{sev_icon}]{c.reset} "
            f"{c.bold}{finding.file}:{finding.line}{c.reset} "
            f"{c.dim}({finding.kind.value}){c.reset}"
        )

        # Line 2: message
        msg = f"      {finding.message}"

        lines = [header, msg]

        # Optional: claude_hint
        if finding.claude_hint and finding.claude_hint != finding.message:
            lines.append(f"      {c.cyan}→ {finding.claude_hint}{c.reset}")

        # Optional: fix_suggestion
        if finding.fix_suggestion:
            lines.append(f"      {c.green}fix: {finding.fix_suggestion}{c.reset}")

        # Optional: validator reasoning + confidence (kind=validator only)
        if finding.kind.value == "validator" and finding.reasoning:
            conf = f" [{finding.confidence}]" if finding.confidence else ""
            lines.append(f"      {c.dim}reasoning{conf}: {finding.reasoning[:200]}{c.reset}")

        # Optional: rule_id + doc_link
        footer_parts = []
        if finding.rule_id:
            footer_parts.append(finding.rule_id)
        if finding.doc_link:
            footer_parts.append(finding.doc_link)
        if footer_parts:
            lines.append(f"      {c.dim}{' · '.join(footer_parts)}{c.reset}")

        return lines
