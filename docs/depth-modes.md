# Depth modes

Depth controls how much work tailtest does on every Claude edit. Pick the depth that matches your risk tolerance and latency budget.

## The five modes

| Mode | Tests | Secrets | SAST | Coverage | Validator | Red team | LLM calls |
|---|---|---|---|---|---|---|---|
| `off` | — | — | — | — | — | — | no |
| `quick` | impacted | yes | — | — | — | — | no |
| `standard` | impacted | yes | yes | — | — | — | no |
| `thorough` | impacted | yes | yes | delta | yes | — | yes |
| `paranoid` | impacted | yes | yes | delta | yes | yes (agent projects) | yes |

"LLM calls" means tailtest invokes `claude -p` for the validator and red-team phases. These cost real money and add latency. Everything at `quick` and `standard` is free and fast.

## Mode details

### `off`

The hook short-circuits immediately. Nothing runs on any edit. Use this when you need tailtest completely out of the way for a session without uninstalling the plugin. Toggle back with `/tailtest:depth standard` when you're ready.

### `quick`

Runs impacted tests and gitleaks only. No SAST, no SCA, no coverage. The fastest hot loop. Good for rapid iteration when you know the codebase is security-clean and you only care whether the tests stay green.

SCA (OSV dependency scan) still fires at `quick` if you edit a manifest (`pyproject.toml`, `package.json`), because missing a new dependency vuln during a manifest edit is a real risk even at low depth.

### `standard`

The default. Runs impacted tests + gitleaks + Semgrep (SAST) + OSV when a manifest changes. The scanner suite that covers the most common risk surface for the cost of a few seconds per edit.

### `thorough`

Adds delta coverage analysis and the Jiminy Cricket validator subagent on top of `standard`. Delta coverage flags exactly which lines your edit added that aren't covered by any test. The validator is a reasoning judge (LLM call via `claude -p`) that reads Claude's diff and checks for logical errors, missing edge cases, and unsafe assumptions.

Use `thorough` on production code, security-sensitive modules, or any time you want a second opinion on Claude's reasoning, not just whether the tests pass.

### `paranoid`

Everything in `thorough`, plus 64 red-team LLM attacks against code classified as `ai_surface: agent`. The red-team runner probes for prompt injection, data exfiltration paths, tool-call forgery, and other LLM-specific attack patterns. Results in a `redteam` finding kind that feeds back into Claude's next turn.

Red-team only fires on projects where the scanner has classified `ai_surface: agent`. Projects without agent code skip the red-team phase even at `paranoid` depth.

`paranoid` is expensive: expect multiple `claude -p` calls per edit and noticeably higher latency. Reserve it for the final hardening pass on agent code before a release.

## How to switch

Use the `/tailtest:depth` skill from inside Claude Code:

```
/tailtest:depth quick
/tailtest:depth standard
/tailtest:depth thorough
/tailtest:depth paranoid
/tailtest:depth off
```

This writes `depth: <mode>` to `.tailtest/config.yaml` and takes effect on the next edit. No restart required.

You can also edit `.tailtest/config.yaml` directly:

```yaml
schema_version: 1
depth: thorough
```

## Persistence

The depth setting lives in `.tailtest/config.yaml` and persists across sessions. If you commit that file, every contributor shares the same depth. If you want a per-session override without touching the committed config, use `/tailtest:depth` -- it writes to the same file, so commit carefully.
