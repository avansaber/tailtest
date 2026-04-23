# tailtest

> You build. We cover.

tailtest is a Claude Code plugin that automatically runs tests every time Claude writes or edits a source file -- no prompting required.

**[Full documentation at tailtest.com/docs](https://tailtest.com/docs)**

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

Python, TypeScript, JavaScript, Go, Ruby, PHP, Java, Kotlin, Rust

Runners are auto-detected from `pyproject.toml`, `package.json`, `go.mod`, `build.gradle.kts`, and other standard manifests.

---

## Quick config

Create `.tailtest/config.json` in your project root to control depth:

```json
{ "depth": "standard" }
```

Options: `simple` (2-3 scenarios), `standard` (5-8, default), `thorough` (10-15).

See [tailtest.com/docs/config](https://tailtest.com/docs/config) for all options.

---

## License

MIT
