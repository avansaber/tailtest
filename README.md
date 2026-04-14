# tailtest

> You build. We cover.

tailtest is a Claude Code plugin that automatically runs the test cycle you would otherwise ask Claude for manually. Every time Claude writes or edits a source file, tailtest generates production-like scenarios for what was just built, executes them, and surfaces only what fails -- without you asking.

When everything passes, tailtest is silent. When something fails, it shows you one line and asks if you want it fixed.

---

## Install

```
/plugin install avansaber/tailtest
```

Restart Claude Code after install. No other setup required.

---

## What it does

After any Claude-written file lands in your project:

1. tailtest generates scenarios that describe real business behavior -- not function signatures
2. It executes them using your existing test runner (pytest, vitest, jest) if available, or falls back to direct execution, or to simulation if neither is available
3. If all pass: silence
4. If something fails: one line surfaced, "want me to fix this?"

**Example:** Claude builds a billing service. tailtest generates: "Create invoice at $800 against a $1,000 credit limit -- verify it succeeds. Create invoice at $1,200 -- verify it is rejected." Runs them. If the credit-limit check has a bug, you see it before you move on.

---

## Configuration

Create `.tailtest/config.json` in your project root (optional):

```json
{
  "depth": "standard",
  "timeout": 30
}
```

| Key | Values | Default | What it does |
|---|---|---|---|
| `depth` | `simple`, `standard`, `thorough` | `standard` | Controls scenario count: simple=2-3 (happy path), standard=5-8 (+ edge cases), thorough=10-15 (+ failure modes) |
| `timeout` | integer (seconds) | `30` | Max seconds before a test run is considered hung |

---

## Escape hatch

Add a `.tailtest-ignore` file at your project root (gitignore syntax) to silence specific paths:

```
scripts/
generated/*.py
fixtures/
```

---

## Session state

tailtest writes `.tailtest/session.json` during each Claude Code session. This file tracks pending files, runner detection results, and fix attempt counts. It is gitignored automatically.

Schema: [`hooks/session.schema.json`](hooks/session.schema.json)

---

## What tailtest does NOT do

- Security scanning
- Coverage percentages or delta tracking
- HTML reports or dashboards
- Standalone CLI -- this is a Claude Code plugin, not a command-line tool

---

## License

Apache 2.0
