---
description: Run the tailtest project scanner and present what it found. Shows detected languages, runners, frameworks, AI surface classification, and the likely_vibe_coded flag.
argument-hint: --deep (optional, requests the LLM-backed deep scan when available)
---

# /tailtest:scan

When the user invokes this skill, invoke the `scan_project` MCP tool and present the result as a short readable summary.

## How to run the scan

Call the `scan_project` MCP tool. The tool accepts a boolean `deep` parameter.

- If `$ARGUMENTS` contains `--deep`, pass `deep: true`. Note that deep scan ships in a later release; today it falls back to the shallow scan.
- Otherwise, pass `deep: false` (the default shallow scan).

The shallow scan runs in under 5 seconds on projects up to 10k files. It walks manifests + src/tests trees, detects runners + frameworks + AI surfaces, and writes the result to `.tailtest/profile.json`.

## What to present

Build a short summary (max 10 lines) from the scan result:

1. Primary language + file counts per language
2. Detected test runners (pytest, vitest, jest, etc.)
3. Detected frameworks and infrastructure (fastapi, django, next.js, docker, CI presence)
4. AI surface classification: `none`, `utility`, or `agent`, plus the confidence level
5. The `likely_vibe_coded` flag when true (plan-file presence signals a vibe-coded project)
6. Total files walked + scan duration
7. Scan status (ok, partial, failed)

Example output:

```
tailtest scan: python / 80 files / pytest
frameworks: fastapi, pytest
ai surface: agent (high confidence, 3 imports across 2 files)
likely vibe-coded: true (CLAUDE.md, AGENTS.md present)
scanned 80 files in 28ms, status ok
```

## What not to do

- Do not read the raw `.tailtest/profile.json` and dump it. The point is a summary, not a JSON listing.
- Do not run `tailtest scan` as a subprocess. Use the MCP tool, which already handles the scanner invocation and the cache.
- Do not promise deep-scan LLM output today. If `--deep` is requested, note that today it returns the shallow result, and the full LLM pass ships later.

## Related skills

- `/tailtest:status` for a compact status summary
- `/tailtest:gen` to generate tests for a source file
- `/tailtest:setup` for the onboarding interview
