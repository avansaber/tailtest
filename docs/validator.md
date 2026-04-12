# Validator subagent (Jiminy Cricket)

At `thorough` and `paranoid` depth, tailtest runs a reasoning subagent after the normal test + security pass. Its job is to catch what deterministic tools miss -- behavioral regressions, missed edge cases, and security implications that only show up when someone reasons about the code.

It never writes code. It only reports findings.

## When it fires

The validator runs automatically as part of the PostToolUse hot loop. No slash command is needed.

| Depth | Validator fires? |
|-------|----------------|
| `off` | No |
| `quick` | No |
| `standard` | No |
| `thorough` | Yes |
| `paranoid` | Yes (plus red-team) |

Set depth with `/tailtest:depth thorough` or add `depth: thorough` to `.tailtest/config.yaml`.

## What it checks

For every file edit, the validator:

1. **Reads the changed files** -- the full current state, not just the diff.
2. **Finds the relevant tests** -- searches for tests that import or reference the changed symbols.
3. **Reasons about correctness** -- asks whether the change preserves the function's contract, introduces untested edge cases, or interacts with state in a way that passing tests won't catch.
4. **Checks security implications** -- for changes touching auth, input handling, file paths, subprocesses, or external data, looks for injection, path traversal, or information disclosure.

It does NOT check style, linting, or design. Those are the developer's domain.

## Findings

Validator findings appear in the PostToolUse context injected into Claude's next turn and in the HTML report at `.tailtest/reports/latest.html`. They look like this in the report:

```
validator · high · src/auth/middleware.py:42
  Token is logged on the error path (line 42). An attacker with log access could extract valid tokens.
  fix: remove token from log format string, or log only the first 4 chars
  confidence: high
```

### Severity levels

| Severity | Meaning |
|----------|---------|
| `critical` | Likely security vulnerability or data-loss bug |
| `high` | Probable behavioral regression or security concern |
| `medium` | Possible issue; warrants review |
| `low` | Likely harmless, but worth knowing |
| `info` | Observation, not a bug |

The validator is honest about confidence. A `high` severity finding with `low` confidence means "this pattern looks dangerous but I'm not certain it's reachable."

## Interpreting findings

Validator findings are reasoning-based. Unlike lint rules, they can catch logic errors that no static rule would catch -- but they can also produce false positives, especially when:

- The changed code has complex invariants that the validator can't fully trace.
- The validator can't see external state (databases, network) that the code depends on.
- The change is refactoring-only with no semantic difference.

If you see a validator finding that you believe is a false positive, baseline it. Baselined findings stay silent in future runs.

## Baselineing validator findings

```
/tailtest:debt
```

This opens the baseline manager. From there you can add the validator finding to `.tailtest/baseline.yaml`. Once baselined, it won't appear in future runs unless the relevant code changes significantly.

You can also edit `.tailtest/baseline.yaml` directly -- the format is the same as other finding types.

## Validator memory

The validator builds up a project-specific memory file at `.tailtest/memory/validator.md`. After each run, it appends a dated note about what it validated and what it found. This lets it carry context across sessions -- for example, remembering that a particular pattern in your codebase is intentional.

To view or clear the memory file:

```
/tailtest:memory
/tailtest:memory clear
```

## Cost and latency

Each validator run makes one LLM API call (Claude Sonnet). At `thorough` depth on a typical file edit, this adds 3-8 seconds and one API call to the hot loop. The call uses your Anthropic account or Claude Code subscription; the same data-privacy guarantees that apply to your Claude Code session apply to the validator call.

If the cost or latency is a concern, switch to `standard` depth:

```
/tailtest:depth standard
```
