"""HTMLReporter, self-contained HTML report for a ``FindingBatch`` (Phase 2 Task 2.6).

Produces a single HTML file with inline CSS and NO external
dependencies: no framework, no CDN, no external fonts, no remote
images. A reader can open the report in any browser offline and
get the full picture. The file is written to
``.tailtest/reports/<iso-timestamp>.html`` and mirrored to
``.tailtest/reports/latest.html`` so the ``/tailtest:report``
skill can point at a stable path.

Design notes:

- **Escaping first.** Every string that originated from outside
  tailtest (finding messages, file paths, rule ids, claude
  hints, auto-offer suggestions) runs through ``html.escape()``
  before it lands in the HTML body. Phase 1's Finding schema
  does not guarantee HTML-safe content, and gitleaks +
  Semgrep + OSV responses can contain arbitrary UTF-8. Failing
  to escape would open an XSS surface the moment a user opens
  the report in their browser.
- **No JavaScript.** The Phase 2 dashboard is static on purpose.
  Phase 4 adds a live server, which is where interactive
  filtering and sorting will live. Shipping JS now would mean
  either inlining a large framework (no thanks) or writing
  hand-rolled vanilla JS that duplicates Phase 4's work.
- **Findings grouped by kind.** Tests first (the user's primary
  concern), then security (secret / SAST / SCA), then coverage
  gaps, then lint/ai_surface/validator/redteam. Baselined
  findings render in a separate "Suppressed" section at the
  bottom so the "what's new" view stays clean.
- **Minimal styling.** No dark mode toggle, no color themes.
  Severity is conveyed by a colored stripe on the left edge of
  each finding card, plus a label badge. Readable on a laptop,
  printable on A4 without clipping.
- **Stable layout.** File sizes are bounded by the finding count,
  not by the content length. Long messages get wrapped; long
  file paths get broken on path separators.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind, Severity

logger = logging.getLogger(__name__)


# Order in which kinds render. Tests first because they are the
# primary hot-loop signal. Security next. Everything else after.
_KIND_ORDER: tuple[FindingKind, ...] = (
    FindingKind.TEST_FAILURE,
    FindingKind.SECRET,
    FindingKind.SAST,
    FindingKind.SCA,
    FindingKind.COVERAGE_GAP,
    FindingKind.LINT,
    FindingKind.AI_SURFACE,
    FindingKind.VALIDATOR,
    FindingKind.REDTEAM,
)


_KIND_LABELS: dict[FindingKind, str] = {
    FindingKind.TEST_FAILURE: "Test failures",
    FindingKind.SECRET: "Secrets",
    FindingKind.SAST: "Static analysis (SAST)",
    FindingKind.SCA: "Dependency advisories (SCA)",
    FindingKind.COVERAGE_GAP: "Coverage gaps",
    FindingKind.LINT: "Lint",
    FindingKind.AI_SURFACE: "AI surface",
    FindingKind.VALIDATOR: "Validator",
    FindingKind.REDTEAM: "Red team",
}


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


@dataclass(frozen=True)
class HTMLReportPaths:
    """Return value from ``HTMLReporter.write_report``.

    ``timestamped`` is the ``<iso>.html`` file, unique per run.
    ``latest`` is the ``latest.html`` file that the report skill
    and any static web server watches. Both are plain copies:
    we don't use a symlink because Windows filesystems don't
    reliably support them for non-admin users.
    """

    timestamped: Path
    latest: Path


class HTMLReporter:
    """Render a ``FindingBatch`` into a self-contained HTML document.

    Parameters
    ----------
    tool_version:
        Version string written into the footer. Defaults to a
        static "0.1.0-alpha" label; callers should override with
        the real ``tailtest.__version__`` when they have access.
    now_utc:
        Injected timestamp for deterministic tests. When None the
        current UTC time is used.
    """

    def __init__(
        self,
        *,
        tool_version: str = "0.1.0-alpha",
        now_utc: datetime | None = None,
    ) -> None:
        self.tool_version = tool_version
        self._now_utc = now_utc

    # --- Public API -------------------------------------------------

    def render(self, batch: FindingBatch) -> str:
        """Return the HTML document as a string. Pure and testable."""
        return self._build_html(batch)

    def write(self, batch: FindingBatch, out_path: Path) -> Path:
        """Render and write the HTML document to ``out_path``.

        Creates parent directories as needed. Writes atomically
        via a ``.tmp`` file + rename so concurrent readers never
        see a half-written report. Returns the resolved path.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        text = self._build_html(batch)
        tmp = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(out_path)
        return out_path

    def write_report(self, batch: FindingBatch, reports_dir: Path) -> HTMLReportPaths:
        """Write both the timestamped file and the ``latest.html`` mirror.

        ``reports_dir`` is typically ``<project_root>/.tailtest/reports``.
        The timestamped file uses a filesystem-safe ISO format
        (colons replaced with hyphens), and ``latest.html`` is a
        plain copy of the same text. Both files are written
        atomically.
        """
        reports_dir = Path(reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)
        now = self._now_utc or datetime.now(UTC)
        stamp = now.strftime("%Y-%m-%dT%H-%M-%SZ")
        timestamped = reports_dir / f"{stamp}.html"
        latest = reports_dir / "latest.html"

        text = self._build_html(batch)
        for target in (timestamped, latest):
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(target)

        return HTMLReportPaths(timestamped=timestamped, latest=latest)

    # --- Top-level HTML assembly ------------------------------------

    def _build_html(self, batch: FindingBatch) -> str:
        parts: list[str] = []
        parts.append("<!DOCTYPE html>")
        parts.append('<html lang="en">')
        parts.append("<head>")
        parts.append('  <meta charset="utf-8">')
        parts.append('  <meta name="viewport" content="width=device-width, initial-scale=1">')
        title = f"tailtest report - {html.escape(batch.run_id[:8])}"
        parts.append(f"  <title>{title}</title>")
        parts.append(f"  <style>{_CSS}</style>")
        parts.append("</head>")
        parts.append("<body>")

        parts.append(self._render_header(batch))
        parts.append(self._render_test_summary(batch))
        parts.append(self._render_delta_coverage(batch))
        parts.append(self._render_findings_by_kind(batch))
        parts.append(self._render_baseline(batch))
        parts.append(self._render_footer(batch))

        parts.append("</body>")
        parts.append("</html>")
        return "\n".join(parts)

    # --- Sections ---------------------------------------------------

    def _render_header(self, batch: FindingBatch) -> str:
        summary = html.escape(batch.summary_line or "tailtest report")
        depth = html.escape(batch.depth or "unknown")
        timestamp = (self._now_utc or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M:%S UTC")
        run_id = html.escape(batch.run_id)
        return (
            "<header>"
            "  <h1>tailtest report</h1>"
            f'  <p class="summary">{summary}</p>'
            '  <p class="meta">'
            f"    run <code>{run_id}</code>"
            f"    · depth <code>{depth}</code>"
            f"    · {html.escape(timestamp)}"
            "  </p>"
            "</header>"
        )

    def _render_test_summary(self, batch: FindingBatch) -> str:
        """Render the passed/failed/skipped counts table.

        Rendered even when there were no tests so the template
        layout stays stable and printable.
        """
        total = batch.tests_passed + batch.tests_failed + batch.tests_skipped
        return (
            '<section class="test-summary">'
            "  <h2>Tests</h2>"
            '  <table class="counts">'
            "    <thead><tr>"
            "      <th>Passed</th><th>Failed</th><th>Skipped</th><th>Total</th>"
            "    </tr></thead>"
            "    <tbody><tr>"
            f'      <td class="num pass">{batch.tests_passed}</td>'
            f'      <td class="num fail">{batch.tests_failed}</td>'
            f'      <td class="num skip">{batch.tests_skipped}</td>'
            f'      <td class="num total">{total}</td>'
            "    </tr></tbody>"
            "  </table>"
            "</section>"
        )

    def _render_delta_coverage(self, batch: FindingBatch) -> str:
        """Render the delta coverage block when present.

        Empty string when the runner did not compute delta
        coverage this run. Phase 1 Task 1.8a populates this for
        PythonRunner runs with a diff envelope.
        """
        if batch.delta_coverage_pct is None:
            return ""
        pct = batch.delta_coverage_pct
        uncovered_count = len(batch.uncovered_new_lines)
        parts: list[str] = [
            '<section class="delta-coverage">',
            "  <h2>Delta coverage</h2>",
            f"  <p>New lines covered: <strong>{pct:.1f}%</strong>"
            f" ({uncovered_count} uncovered).</p>",
        ]
        if uncovered_count > 0:
            parts.append("  <ul>")
            for entry in batch.uncovered_new_lines[:10]:
                file_str = html.escape(str(entry.get("file", "?")))
                line_num = entry.get("line", 0)
                parts.append(f"    <li><code>{file_str}:{line_num}</code></li>")
            if uncovered_count > 10:
                parts.append(f'    <li class="more">{uncovered_count - 10} more</li>')
            parts.append("  </ul>")
        parts.append("</section>")
        return "\n".join(parts)

    def _render_findings_by_kind(self, batch: FindingBatch) -> str:
        """Render each non-baseline finding, grouped by kind.

        Kinds render in ``_KIND_ORDER``. Groups with zero
        findings are omitted entirely so the report stays
        compact on a small batch.
        """
        new_findings = [f for f in batch.findings if not f.in_baseline]
        if not new_findings:
            return (
                '<section class="findings empty">'
                "  <h2>Findings</h2>"
                '  <p class="empty-state">No new findings in this run.</p>'
                "</section>"
            )

        by_kind: dict[FindingKind, list[Finding]] = {}
        for f in new_findings:
            by_kind.setdefault(f.kind, []).append(f)

        parts: list[str] = ['<section class="findings">', "  <h2>Findings</h2>"]
        for kind in _KIND_ORDER:
            group = by_kind.get(kind, [])
            if not group:
                continue
            # Sort within the group by severity desc, then file, then line.
            group.sort(
                key=lambda f: (
                    -_SEVERITY_RANK.get(f.severity, 0),
                    str(f.file),
                    f.line,
                )
            )
            label = html.escape(_KIND_LABELS[kind])
            count = len(group)
            parts.append('  <div class="kind-group">')
            parts.append(f'    <h3>{label} <span class="kind-count">({count})</span></h3>')
            for f in group:
                parts.append(self._render_finding_card(f))
            parts.append("  </div>")
        parts.append("</section>")
        return "\n".join(parts)

    def _render_finding_card(self, finding: Finding) -> str:
        """Render one finding as a card with a severity stripe."""
        severity = finding.severity.value
        location = html.escape(str(finding.file))
        if finding.line:
            location = f"{location}:{finding.line}"
        if finding.col is not None and finding.col > 0:
            location += f":{finding.col}"
        message = html.escape(finding.message or "")
        rule_id = html.escape(finding.rule_id or "")
        claude_hint = html.escape(finding.claude_hint or "")
        doc_link = finding.doc_link or finding.advisory_url

        extra_meta: list[str] = []
        if finding.cvss_score is not None and finding.cvss_score > 0:
            extra_meta.append(f"CVSS {finding.cvss_score:.1f}")
        if finding.cwe_id:
            extra_meta.append(html.escape(finding.cwe_id))
        if finding.package_name:
            pkg = html.escape(finding.package_name)
            if finding.package_version:
                pkg += f"@{html.escape(finding.package_version)}"
            extra_meta.append(pkg)
        if finding.fixed_version:
            extra_meta.append(f"fix: {html.escape(finding.fixed_version)}")

        parts: list[str] = [
            f'    <article class="finding sev-{severity}">',
            f'      <header><span class="sev-label">{severity}</span>',
            f'        <code class="location">{location}</code>',
        ]
        if rule_id:
            parts.append(f'        <span class="rule-id">{rule_id}</span>')
        parts.append("      </header>")
        parts.append(f'      <p class="message">{message}</p>')
        if claude_hint:
            parts.append(f'      <p class="hint">hint: {claude_hint}</p>')
        if extra_meta:
            parts.append(
                '      <p class="extra">'
                + " · ".join(f"<span>{m}</span>" for m in extra_meta)
                + "</p>"
            )
        if doc_link:
            safe_link = html.escape(doc_link, quote=True)
            parts.append(
                f'      <p class="doc-link">'
                f'<a href="{safe_link}" rel="noopener nofollow">advisory</a>'
                "</p>"
            )
        parts.append("    </article>")
        return "\n".join(parts)

    def _render_baseline(self, batch: FindingBatch) -> str:
        """Summarize baselined findings in a collapsed block.

        Shows the counts only; the details stay hidden behind
        the Phase 1 ``latest.json`` file so the HTML report does
        not get crowded with "old" noise.
        """
        baselined = [f for f in batch.findings if f.in_baseline]
        if not baselined:
            return ""
        return (
            '<section class="baseline">'
            "  <h2>Suppressed by baseline</h2>"
            f"  <p>{len(baselined)} finding(s) were suppressed because they match"
            " an entry in <code>.tailtest/baseline.json</code>."
            "  Run <code>/tailtest:debt</code> or inspect"
            " <code>.tailtest/reports/latest.json</code> to review them.</p>"
            "</section>"
        )

    def _render_footer(self, batch: FindingBatch) -> str:
        duration = f"{batch.duration_ms / 1000.0:.1f}"
        return (
            "<footer>"
            f"  <p>Generated by tailtest v{html.escape(self.tool_version)}"
            f" in {duration}s.</p>"
            "</footer>"
        )


# --- Inline CSS --------------------------------------------------------

_CSS = """
  :root {
    --fg: #1a1a1a;
    --bg: #f7f7f8;
    --card: #ffffff;
    --muted: #6b7280;
    --border: #e5e7eb;
    --pass: #16a34a;
    --fail: #dc2626;
    --skip: #9ca3af;
    --sev-critical: #991b1b;
    --sev-high: #dc2626;
    --sev-medium: #d97706;
    --sev-low: #2563eb;
    --sev-info: #6b7280;
    --code-bg: #f3f4f6;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px 16px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    color: var(--fg);
    background: var(--bg);
  }
  body > * {
    max-width: 900px;
    margin-left: auto;
    margin-right: auto;
  }
  header { margin-bottom: 24px; }
  h1 { font-size: 22px; margin: 0 0 8px 0; }
  h2 { font-size: 16px; margin: 24px 0 12px 0; padding-bottom: 4px; border-bottom: 1px solid var(--border); }
  h3 { font-size: 14px; margin: 16px 0 8px 0; color: var(--muted); font-weight: 600; }
  .summary { font-size: 15px; font-weight: 500; margin: 4px 0; }
  .meta { color: var(--muted); font-size: 12px; margin: 4px 0; }
  code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    background: var(--code-bg);
    padding: 2px 4px;
    border-radius: 3px;
  }
  section { background: var(--card); padding: 16px; border-radius: 6px; margin-bottom: 16px; border: 1px solid var(--border); }
  table.counts { width: 100%; border-collapse: collapse; }
  table.counts th { text-align: left; font-size: 11px; text-transform: uppercase; color: var(--muted); padding: 4px 8px; }
  table.counts td.num { font-size: 20px; font-weight: 600; padding: 8px; }
  table.counts td.pass { color: var(--pass); }
  table.counts td.fail { color: var(--fail); }
  table.counts td.skip { color: var(--skip); }
  .kind-count { color: var(--muted); font-weight: 400; font-size: 12px; }
  .finding {
    border-left: 4px solid var(--border);
    background: var(--card);
    padding: 8px 12px;
    margin: 8px 0;
    border-radius: 4px;
    border-top: 1px solid var(--border);
    border-right: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }
  .finding.sev-critical { border-left-color: var(--sev-critical); }
  .finding.sev-high { border-left-color: var(--sev-high); }
  .finding.sev-medium { border-left-color: var(--sev-medium); }
  .finding.sev-low { border-left-color: var(--sev-low); }
  .finding.sev-info { border-left-color: var(--sev-info); }
  .finding header { margin: 0; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .sev-label {
    font-size: 10px;
    text-transform: uppercase;
    font-weight: 700;
    padding: 2px 6px;
    border-radius: 3px;
    color: white;
    background: var(--sev-info);
  }
  .sev-critical .sev-label { background: var(--sev-critical); }
  .sev-high .sev-label { background: var(--sev-high); }
  .sev-medium .sev-label { background: var(--sev-medium); }
  .sev-low .sev-label { background: var(--sev-low); }
  .location { flex: 1; }
  .rule-id { color: var(--muted); font-size: 11px; }
  .message { margin: 6px 0; }
  .hint { margin: 4px 0; color: var(--muted); font-size: 12px; font-style: italic; }
  .extra { margin: 4px 0; font-size: 11px; color: var(--muted); }
  .extra span { margin-right: 8px; }
  .doc-link { margin: 4px 0; font-size: 12px; }
  .doc-link a { color: var(--sev-low); text-decoration: none; }
  .doc-link a:hover { text-decoration: underline; }
  .empty-state { color: var(--muted); font-style: italic; }
  footer { color: var(--muted); font-size: 11px; text-align: center; padding: 16px 0; border-top: 1px solid var(--border); margin-top: 24px; }
  ul { padding-left: 20px; }
  li { margin: 2px 0; }
  .more { color: var(--muted); font-style: italic; }
  @media print {
    body { background: white; padding: 0; }
    section { border: none; page-break-inside: avoid; }
  }
""".strip()
