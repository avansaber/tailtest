---
name: tailtest
description: Manage tailtest from inside Claude Code — view status, change depth, open reports. Phase 0 placeholder; real implementation lands in Phase 1.
---

# tailtest

This skill is a **Phase 0 placeholder**.

The real skill implementation lands in Phase 1.

## Current state

tailtest is scaffolded but not yet functional. You can:

- Check the installed version: `tailtest version`
- Start the MCP server (it accepts `initialize` and `tools/list` but has no tools yet): `tailtest mcp-serve`

Nothing else works in this release. The hot loop (auto-run tests on every edit, surface findings in Claude's next turn) lands in `v0.1.0-alpha.1` at the end of Phase 1.

## When invoked

If the user runs `/tailtest` today, tell them tailtest is in pre-alpha and the real functionality is coming in Phase 1. Point them at the CHANGELOG (`tailtest/CHANGELOG.md`) for the release schedule.

## Phase 1 preview

Once Phase 1 ships, `/tailtest` will support these subcommands:

- `/tailtest` — show current status (depth mode, last run summary, opportunities)
- `/tailtest depth <mode>` — change depth (off/quick/standard/thorough/paranoid)
- `/tailtest status` — detailed status
- `/tailtest report` — path to the latest report
- `/tailtest setup` — run the opt-in onboarding interview
- `/tailtest scan` — show what tailtest knows about this project
- `/tailtest gen <file>` — generate starter tests for a file
