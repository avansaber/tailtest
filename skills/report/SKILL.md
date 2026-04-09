---
description: Open the latest tailtest report for detailed review of findings, test results, delta coverage, and any uncovered new lines. Prefers the on-disk HTML view when available, falls back to the JSON.
---

# /tailtest:report

When the user invokes this skill, present the detailed view of the most recent tailtest run from the current project root. Prefer the HTML mirror at `.tailtest/reports/latest.html` because it is already formatted for humans; fall back to parsing `.tailtest/reports/latest.json` when HTML is missing.

## What to read

1. Check `.tailtest/reports/latest.html`. If it exists, tell the user it is ready and print the absolute path so they can `open` it in a browser. Example: `Report ready at /abs/path/.tailtest/reports/latest.html — open it in your browser with "open" or similar.`
2. Then read `.tailtest/reports/latest.json` to produce an inline summary in the conversation, since the user may not have a browser handy.

Both files are written by every `tailtest run` invocation and by the PostToolUse hook. The JSON shape is the `FindingBatch` schema:

- `run_id`
- `depth` (off, quick, standard, thorough, paranoid)
- `duration_ms`
- `summary_line`
- `tests_passed`, `tests_failed`, `tests_skipped`
- `findings` list (test failures, secrets, SAST, SCA, lint, coverage gaps, etc.)
- `delta_coverage_pct`, `uncovered_new_lines`

If neither file exists: tell the user "no tailtest report yet. Run `tailtest run` or edit a file to trigger the PostToolUse hook, then invoke this skill again."

## What to present

A readable summary in this order:

1. **HTML path banner**: if `latest.html` exists, lead with its absolute path so the user can open it. Format: `HTML report: /abs/path/.tailtest/reports/latest.html` on its own line.
2. **Summary line**: copy the `summary_line` field from `latest.json` verbatim (the reporter already wrote it in user-friendly form)
3. **Depth + duration**: one line with depth mode and total duration in seconds
4. **Test counts**: passed / failed / skipped
5. **Findings** (at most 10): ordered by severity (critical first, info last), each with severity, file:line, message, claude_hint if present. Group by kind: test failures first, then secrets, then SAST, then SCA, then coverage gaps, then everything else.
6. **Delta coverage**: percentage + uncovered new lines (at most 5 shown)
7. **Follow-up suggestions**: tell the user what to do about the findings they see (run the failing test in isolation, review the coverage gaps, rotate a leaked secret, upgrade a vulnerable dep, etc.)

If there are more than 10 findings, truncate and note "N more findings in the HTML report at .tailtest/reports/latest.html".

## What not to do

- Do not dump the raw JSON or raw HTML. The skill is a human-readable summary, not a file cat.
- Do not re-run the tests. This skill is read-only against the latest run.
- Do not offer to delete the report file. If the user wants to clear history, they do that manually.
- Do not invoke the `run_tests` MCP tool.
- Do not open the HTML file yourself via a subprocess; surface the path and let the user decide.

## Related skills

- `/tailtest:status` for a compact status summary
- `/tailtest:gen` to generate tests for uncovered code
