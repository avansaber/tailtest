# tailtest

> **Pre-alpha scaffolding.** The product does not work yet.

> *"tailtest grows with your project, from smoke tests to red team. Runs inside Claude Code, never blocks, never lies."*

tailtest is the test + security validator that lives inside Claude Code. It watches every edit the AI agent makes, runs the tests that matter, catches the security issues that matter, and reports findings back into Claude's next turn so the agent can self-correct — without ever blocking or interrupting your work.

This repository holds the Claude Code plugin, the MCP server, the Python engine, and the CLI. It is currently in **Phase 0** (foundation scaffolding). Nothing is functional yet.

## Status

**Version:** `0.1.0-alpha.0` (pre-alpha; nothing works yet)
**Phase:** 0 — Foundation
**First functional release target:** `0.1.0-alpha.1` (end of Phase 1 — MVP hot loop)
**Public release target:** `0.1.0` (end of Phase 7 — launch)

If you are reading this because you want to try tailtest: come back after `0.1.0`. This README exists so the repo scaffolding has a home, not because anything is usable yet.

## What tailtest will do (when it ships)

- **Run your tests automatically** on every Claude Code edit, using your native test runner (pytest, jest, vitest, cargo test, etc.)
- **Generate starter tests** for code Claude just wrote if none exist — in your project's native test framework, never committed automatically
- **Scan for security issues** on the same edit: hardcoded secrets, SAST findings, vulnerable dependencies
- **Feed findings back into Claude's next turn** as structured output, so Claude can fix them in the same session without you having to prompt
- **Grow with your project**: start at `quick` depth for a 50-line script, graduate to `paranoid` depth for a production SaaS, with the user controlling every escalation
- **Never block your work.** tailtest reports; you decide

## Installation

> Once `0.1.0` ships, installation will be:
>
> ```
> claude plugin marketplace add github:avansaber/tailtest
> claude plugin install tailtest@avansaber
> ```
>
> or, for standalone CLI / MCP server use:
>
> ```
> pip install tailtester
> ```

Neither works today. The PyPI package name is `tailtester` (the importable Python package is `tailtest`; the PyPI name is a historical squat that stuck).

## Distribution channels (planned)

- **Claude Code plugin** — `claude plugin install tailtest@avansaber` — full experience (hooks + skills + subagents + MCP + dashboard)
- **Standalone MCP server** — `pip install tailtester && tailtest mcp-serve` — MCP tools only, for Cursor / Windsurf / Codex / any MCP-aware IDE
- **Standalone CLI** — `tailtest run`, `tailtest scan`, `tailtest doctor` — for CI pipelines, Vim/Emacs users, raw terminal use

## Repository layout

```
tailtest/
├── .claude-plugin/plugin.json   Claude Code plugin manifest
├── .mcp.json                    MCP server wiring
├── hooks/                       PostToolUse / SessionStart / Stop hook scripts
├── skills/                      User-invocable slash commands
├── agents/                      Subagent definitions (Phase 5+)
├── src/tailtest/                Python package source
├── tests/                       Unit + integration + e2e tests
├── docs/                        User-facing documentation (Phase 7)
├── examples/                    Example projects (Phase 7)
├── data/                        Static data (red-team attack catalog, etc.)
├── .github/workflows/           CI
├── pyproject.toml
├── README.md                    This file
├── LICENSE                      Apache 2.0
├── CHANGELOG.md
├── CONTRIBUTING.md
└── SECURITY.md
```

## Contributing

The project is in foundation phase and not yet accepting external contributions. `CONTRIBUTING.md` will be updated when we're ready.

## License

[Apache 2.0](LICENSE). Copyright 2026 AvanSaber Inc.

## Security

See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability. tailtest itself practices what it preaches — every release passes a pre-public repo hygiene audit (secret scan, PII scan, license audit, manual review) before it ships.
