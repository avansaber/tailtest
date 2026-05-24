# tailtest -- AI software testing for Claude Code

[![License: MIT](https://img.shields.io/badge/License-MIT-emerald.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-491_passing-emerald)](https://github.com/avansaber/tailtest)
[![Version](https://img.shields.io/badge/version-3.14.0-blue)](https://github.com/avansaber/tailtest/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS_%7C_Linux-lightgrey)](https://tailtest.com/platform/agent-edits/)

> You build. Claude builds. tailtest makes sure it works.

**tailtest** is the open-source AI software testing layer for [Claude Code](https://www.anthropic.com/claude-code). It runs inside the build loop: every time Claude edits a file, tailtest queues the file, generates production-shaped scenarios using the R1-R15 rule layer, runs them with your existing test runner (pytest, jest, go test, etc.), and surfaces failures back to Claude within the same turn. Hook-based. Deterministic. No prompting required.

Open source (MIT), no telemetry, no SaaS account. Includes adversarial mode (R15) that biases scenario generation toward 8 breakage categories -- the mode we've used to find 16 real bugs across 47 open-source Python repositories.

**[Read more on tailtest.com](https://www.tailtest.com/) · [Platform overview](https://www.tailtest.com/platform/) · [Agent-edit testing deep dive](https://www.tailtest.com/platform/agent-edits/) · [Full docs](https://www.tailtest.com/docs/)**

---

## Install

```bash
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber-tailtest
```

Restart Claude Code after install. No other setup required.

## Update

```bash
claude plugin marketplace update avansaber-tailtest
claude plugin update tailtest@avansaber-tailtest
```

## Uninstall

```bash
claude plugin remove tailtest@avansaber-tailtest
```

---

## How it works

After any Claude-written file lands in your project:

1. tailtest generates scenarios that describe real business behavior -- not function signatures
2. It runs them using your existing test runner (pytest, vitest, jest, go test, and more)
3. Pass: `tailtest: N scenarios -- all passed.` Fail: one line surfaced, "want me to fix this?"

---

## Supported languages

Python, TypeScript, JavaScript, Go, Ruby, PHP, Java, Kotlin, C#, Rust

Runners are auto-detected from `pyproject.toml`, `package.json`, `go.mod`, `build.gradle.kts`, `*.csproj`, `*.sln`, and other standard manifests.

---

## Quick config

Create `.tailtest/config.json` in your project root to control depth:

```json
{ "depth": "standard" }
```

Options: `simple` (2-3 scenarios), `standard` (5-8, default), `thorough` (10-15).

See [tailtest.com/docs/config](https://tailtest.com/docs/config) for all options.

---

## Other tailtest variants

Same R1-R15 rule layer, same adversarial test mode, different host integration. **This repo is the Claude Code variant.**

- **[tailtest](https://github.com/avansaber/tailtest)** -- Claude Code plugin (hook-driven; this repo)
- **[tailtest-cursor](https://github.com/avansaber/tailtest-cursor)** -- Cursor plugin (hook-driven)
- **[tailtest-codex](https://github.com/avansaber/tailtest-codex)** -- Codex CLI plugin (hook-driven)
- **[tailtest-cline](https://github.com/avansaber/tailtest-cline)** -- Cline plugin (MCP-driven; reaches 8+ editors via Cline's host coverage)

See [tailtest.com/demo/claude-code](https://tailtest.com/demo/claude-code) for a live walkthrough of this variant, or [tailtest.com/comparison](https://tailtest.com/comparison) for a feature matrix across all four.

---

## License

MIT
