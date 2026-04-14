# Configuration

tailtest works without any configuration. Everything below is optional.

## Scenario depth

Create `.tailtest/config.json` at your project root to change the scenario depth:

```json
{
  "depth": "standard"
}
```

| Value | Scenario count | What it covers |
|---|---|---|
| `simple` | 2-3 | Happy path only. Good for fast iteration. |
| `standard` | 5-8 | Happy path plus the most important edge cases. Default. |
| `thorough` | 10-15 | Full business rule coverage including all failure modes. |

**When to use `thorough`:** Critical paths -- billing, auth, anything user-facing or money-related. A billing service at `thorough` gets: invoice within limit, invoice over limit, cumulative invoices over limit, zero-balance edge case, concurrent invoice creation. At `simple`, it gets just the first two.

**When to use `simple`:** Rapid prototyping where you want a quick signal and will revisit with more coverage later.

`standard` is the right default for most sessions.

## Silencing specific paths

Add a `.tailtest-ignore` file at your project root. Uses gitignore syntax. Lines starting with `#` are comments.

```
# Ignore the generated API client
generated/
src/api/client.ts

# Ignore seed and migration scripts
scripts/
db/
```

Directory patterns like `scripts/` match any file under `scripts/`, including nested paths. Single-file patterns like `src/api/client.ts` match exactly that file.

The ignore file is checked before all other filter logic. If a path matches any pattern, tailtest skips it without further evaluation.

## What does not need configuration

Language detection, runner detection, test file locations, framework detection, monorepo detection, style matching -- all of this is automatic at session start. None of it requires any configuration.

If tailtest is not behaving as expected for a specific language or project type, see [troubleshooting.md](troubleshooting.md).
