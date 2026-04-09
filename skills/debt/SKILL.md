---
description: Review the accepted debt baseline for this project. Shows findings that tailtest has baselined (hidden from the hot loop) so the user can audit what is being silenced, re-open specific entries, or clean up stale ones.
---

# /tailtest:debt

When the user invokes this skill, read `.tailtest/baseline.yaml` from the current project root and present every baselined finding in a readable form. The baseline file is the "accepted debt" ledger: it records findings that tailtest has agreed to hide from the hot loop summary, typically because they predate the user's current work.

## What to read

Read `.tailtest/baseline.yaml`. Schema (see the file's own header comment for the full explanation):

- `schema_version` (int)
- `generated_at` (ISO timestamp)
- `entries` (list of baseline entries), each with:
  - `id` (16-char stable hash)
  - `kind` (test_failure, secret, sast, sca, lint, coverage_gap, ai_surface)
  - `file` (path)
  - `line` (int)
  - `rule_id` (string or null)
  - `first_seen` (ISO timestamp)
  - `failure_streak` (int, used for flaky-test tracking)
  - `reason` (human-written justification)

If the file does not exist: tell the user "no baseline yet. tailtest populates `.tailtest/baseline.yaml` on the first green run that has pre-existing security or lint findings, or after a test failure streak reaches 3. There is no accepted debt to review yet."

## What to present

A two-part view:

### Part 1: summary

One line per kind: `- <kind>: N entries`. Order: test_failure, secret, sast, sca, lint, coverage_gap, ai_surface. Skip kinds with zero entries. Example:
```
Accepted debt (5 entries total):
- secret: 2 entries
- sast: 2 entries
- sca: 1 entry
```

### Part 2: details grouped by kind

For each kind with at least one entry, show a compact list:
```
Secrets (2):
- src/legacy/config.py:7 - gitleaks::aws-access-token - auto-baselined on first detection - 2026-04-09
- src/legacy/init.py:12 - gitleaks::generic-api-key - auto-baselined on first detection - 2026-04-09
```

Order within each group: file path alphabetically, then line number. Cap the total visible entries at 20; if there are more, append "... N more entries in .tailtest/baseline.yaml" and stop.

### Part 3: follow-ups

End with a short action-oriented paragraph:
- "To remove an entry, delete its block from `.tailtest/baseline.yaml` and commit. A subsequent run that re-detects the finding will NOT re-add it until a green run completes."
- "To update a reason field, edit it directly in the file. tailtest does not overwrite reasons on re-baseline."
- "The baseline is meant to be committed to git so every contributor sees the same accepted-debt set."

## What not to do

- Do not dump the raw YAML. The skill is a human-readable summary, not a file cat.
- Do not offer to clear the baseline file. That is a destructive operation; the user should do it explicitly.
- Do not re-run tailtest. This skill is read-only against the existing baseline.
- Do not invoke any MCP tools.

## Related skills

- `/tailtest:report` for the last run's findings
- `/tailtest:status` for a compact project status
- `/tailtest:scan` to re-scan and potentially discover new findings
