---
description: Open the latest tailtest report for detailed review of findings, test results, delta coverage, and any uncovered new lines. Reads .tailtest/reports/latest.json.
---

# /tailtest:report

When the user invokes this skill, read `.tailtest/reports/latest.json` from the current project root and present the findings in detail.

## What to read

Read `.tailtest/reports/latest.json`. The file is written by every `tailtest run` invocation and by the PostToolUse hook. Its shape is the `FindingBatch` schema:

- `run_id`
- `depth` (off, quick, standard, thorough, paranoid)
- `duration_ms`
- `summary_line`
- `tests_passed`, `tests_failed`, `tests_skipped`
- `findings` list (test failures, secrets, lint, coverage gaps, etc.)
- `delta_coverage_pct`, `uncovered_new_lines`

If the file does not exist: tell the user "no tailtest report yet. Run `tailtest run` or edit a file to trigger the PostToolUse hook, then invoke this skill again."

## What to present

A readable summary in this order:

1. **Summary line**: copy the `summary_line` field verbatim (the terminal reporter already wrote it in user-friendly form)
2. **Depth + duration**: one line with depth mode and total duration in seconds
3. **Test counts**: passed / failed / skipped
4. **Findings** (at most 10): ordered by severity (critical first, info last), each with severity, file:line, message, claude_hint if present
5. **Delta coverage**: percentage + uncovered new lines (at most 5 shown)
6. **Follow-up suggestions**: tell the user what to do about the findings they see (run the failing test in isolation, review the coverage gaps, etc.)

If there are more than 10 findings, truncate and note "N more findings in .tailtest/reports/latest.json".

## What not to do

- Do not dump the raw JSON. The skill is a human-readable summary, not a file cat.
- Do not re-run the tests. This skill is read-only against the latest run.
- Do not offer to delete the report file. If the user wants to clear history, they do that manually.
- Do not invoke the `run_tests` MCP tool.

## Related skills

- `/tailtest:status` for a compact status summary
- `/tailtest:gen` to generate tests for uncovered code

## Phase note

Phase 1 writes `latest.json` only. Phase 2 adds an on-disk HTML report at `.tailtest/reports/<timestamp>.html` for a richer view. When that ships, this skill will prefer the HTML path and offer to open it in the user's browser.
