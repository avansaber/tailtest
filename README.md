# tailtest

> You build. We cover.

tailtest is a Claude Code plugin that automatically runs the test cycle you would otherwise ask Claude for manually. Every time Claude writes or edits a source file, tailtest generates production-like scenarios for what was just built, executes them, and surfaces only what fails -- without you asking.

When everything passes, tailtest is silent. When something fails, it shows you one line and asks if you want it fixed.

---

## Install

```
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber-tailtest
```

Restart Claude Code after install. No other setup required.

## Update

```
claude plugin marketplace update avansaber-tailtest
claude plugin update tailtest@avansaber-tailtest
```

---

## Documentation

- [Quickstart](docs/quickstart.md)
- [How it works](docs/how-it-works.md)
- [Configuration](docs/configuration.md)
- [Supported languages and frameworks](docs/languages.md)
- [Existing projects](docs/existing-projects.md)
- [Monorepo support](docs/monorepo.md)
- [The /t command](docs/slash-command.md)
- [Filter reference](docs/filter-reference.md)
- [Session state](docs/session-state.md)
- [Troubleshooting](docs/troubleshooting.md)

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
  "depth": "standard"
}
```

| Key | Values | Default | What it does |
|---|---|---|---|
| `depth` | `simple`, `standard`, `thorough` | `standard` | Controls scenario count: simple=2-3 (happy path), standard=5-8 (+ edge cases), thorough=10-15 (+ failure modes) |

---

## Escape hatch

Add a `.tailtest-ignore` file at your project root (gitignore syntax) to silence specific paths:

```
scripts/
generated/*.py
fixtures/
```

---

## Supported languages

| Language | Runner | Test style |
|---|---|---|
| Python | pytest | `tests/test_{name}.py` |
| TypeScript | vitest / jest | `__tests__/{name}.test.ts` |
| JavaScript | vitest / jest | `__tests__/{name}.test.js` |
| Go | go test | co-located `{name}_test.go` |
| Ruby | rspec / minitest | `spec/{name}_spec.rb` or `test/{name}_test.rb` |
| PHP (Laravel) | phpunit | `tests/Feature/` or `tests/Unit/` |
| Java | Maven / Gradle | `src/test/java/{Name}Test.java` |
| Rust | cargo test | inline `#[cfg(test)]` module |

For languages that require an explicit test runner (Go, Ruby, PHP, Java, Rust), tailtest is silent when no runner is detected -- it does not generate tests for bare script projects.

---

## Session state

tailtest writes `.tailtest/session.json` during each Claude Code session. This file tracks pending files, runner detection results, and fix attempt counts. Add `.tailtest/` to your project's `.gitignore` to keep it out of version control.

Schema: [`hooks/session.schema.json`](hooks/session.schema.json)

---

## Monorepo support

tailtest automatically detects monorepo layouts (pnpm workspaces, Nx, Turborepo, Lerna, Rush, or multiple package.json/pyproject.toml/composer.json files at subdirectory roots).

For each detected package, tailtest resolves the correct test runner and test location independently. Files in `packages/api/` use that package's runner; files in `packages/web/` use that package's runner. Files outside all packages fall back to the root runner if one is configured.

No configuration needed. Detection is automatic at session start.

---

## Commands

| Command | What it does |
|---|---|
| `/t <file>` | Generate or update tests for any file on demand -- works on existing files tailtest would normally skip |
| `/summary` | Show what tailtest tested this session, what passed, what was fixed, what was deferred |

---

## What tailtest does NOT do

- Security scanning
- Coverage percentages or delta tracking
- HTML reports or dashboards
- Standalone CLI -- this is a Claude Code plugin only, not a command-line tool
- Proactive scanning -- tailtest is reactive; it only processes files Claude actually edits in the current session

---

## License

Apache 2.0
