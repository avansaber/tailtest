---
description: Show a compact status summary of tailtest for the current project. Reads .tailtest/config.yaml, .tailtest/profile.json, and .tailtest/reports/latest.json if they exist and presents them as a 5-line readable block.
---

# /tailtest:status

When the user invokes this skill, present a compact status summary of tailtest for the current project. The user wants the answer in under 10 lines total.

## What to read

Read these files if they exist in the current project root:

1. `.tailtest/config.yaml` for the configured depth mode and notification flags
2. `.tailtest/profile.json` for the project scan result (primary language, detected runners, frameworks, AI surface)
3. `.tailtest/reports/latest.json` for the last test run summary

Missing files are fine. If `.tailtest/` itself does not exist, tell the user "tailtest has not run in this project yet" and suggest invoking `/tailtest:setup` or `tailtest doctor` to initialize it.

## What to present

A compact block with:

1. Depth mode from config (or `standard` if no config)
2. Primary language + the detected runner (e.g. `python / pytest` or `typescript / vitest`)
3. Last run summary from `reports/latest.json`: passed/total count, duration, any failed tests
4. Delta coverage percentage if present (`reports/latest.json` has `delta_coverage_pct` set to a number)
5. One-line next action: run tests, run scan, open a report, etc.

Example output:

```
tailtest: standard depth, python/pytest
last run: 12/14 passed, 2 failed, 1.4s (run_id abc123)
delta coverage: 87.5% (2 new lines uncovered)
2 uncovered entries in .tailtest/reports/latest.json
next: `tailtest run` to re-run or `/tailtest:report` for detail
```

## What not to do

- Do not run any commands. This skill is read-only.
- Do not invoke the `run_tests` MCP tool. That is `/tailtest:gen` or a direct `tailtest run` concern.
- Do not dump the full JSON of any file. The point of the skill is a summary, not a file listing.
- Do not promise features that are not yet shipped. If the user asks about depth modes the current install does not support (`thorough` or `paranoid`), say they will ship in a later release.

## Related skills

- `/tailtest:scan` to re-scan the project profile
- `/tailtest:report` to see the full last-run report
- `/tailtest:security` for security posture and new-vs-baselined counts
- `/tailtest:debt` to review all baselined findings
- `/tailtest:depth` to change depth mode
- `/tailtest:setup` for the opt-in onboarding interview
