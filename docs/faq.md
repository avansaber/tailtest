# FAQ

## Do I need to run `pip install tailtester` to use tailtest?

No. As of the current release, the plugin is self-contained. The bootstrap (`hooks/_bootstrap.py`) looks for the tailtest engine inside the plugin's own `src/` directory first, so the hot loop works with just:

```bash
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber
```

`pip install tailtester` is only needed if you want the **standalone CLI** (`tailtest run`, `tailtest doctor`, `tailtest scan`) outside of Claude Code, or if you want to run tailtest as an MCP server in another IDE. Plugin users do not need pip.

---

## Does tailtest slow down Claude Code?

No. The hook runs asynchronously and always exits 0. It never blocks Claude from making the next edit or generating a response. Findings flow into Claude's next-turn context, but that context injection happens after Claude is already done with the current turn.

At `standard` depth on a typical project, the full hook cycle (impacted tests + gitleaks + Semgrep) takes 1-5 seconds. Claude doesn't wait for it.

---

## Can I use tailtest without Claude Code?

Yes. Install the Python package:

```bash
pipx install tailtester
# or
pip install tailtester
```

This gives you the standalone CLI:

```bash
tailtest scan       # project scanner
tailtest run        # run impacted tests
tailtest doctor     # diagnose install issues
tailtest mcp-serve  # run as an MCP server for Cursor, Windsurf, etc.
```

You can also wire the MCP server into any MCP-aware IDE via `.mcp.json`.

---

## How do I stop tailtest from running?

```
/tailtest:depth off
```

This writes `depth: off` to `.tailtest/config.yaml` and takes effect on the next edit. The hook still fires but short-circuits immediately -- nothing runs. Toggle back with `/tailtest:depth standard` when you're ready.

If you want tailtest gone entirely: `claude plugin uninstall tailtest@avansaber`.

---

## What languages and test runners are supported?

| Language | Runners |
|---|---|
| Python | pytest (via pytest-testmon for impact analysis) |
| JavaScript / TypeScript | vitest, jest, ava, mocha, tape, node:test |
| Rust | cargo test |

tailtest auto-detects which runner your project uses. If you have multiple runners (e.g., pytest for a Python backend and vitest for a JS frontend), both fire on relevant edits.

---

## Does tailtest send my code anywhere?

Mostly no. Two narrow exceptions:

1. **OSV.dev API (SCA):** When you edit a manifest (`pyproject.toml`, `package.json`), tailtest sends your package names and versions to the public OSV.dev API to check for known advisories. No source code is sent -- only package identifiers.

2. **`claude -p` (LLM judge at thorough+ depth):** At `thorough` and `paranoid` depth, tailtest calls `claude -p` for the validator and red-team phases. This sends the relevant code diff to the Claude API. If you are on a plan with data-privacy guarantees, those guarantees apply to these calls. At `standard` depth and below, no LLM calls are made.

No source code, findings, or project metadata is sent to AvanSaber or any third-party telemetry endpoint.

---

## How do I baseline a false positive?

Run `/tailtest:debt` from inside Claude Code. It shows the current baseline (`.tailtest/baseline.yaml`), lets you add new entries, and lets you re-open stale ones. You can also edit `.tailtest/baseline.yaml` directly -- it is a plain YAML file.

For inline suppressions that live in source code rather than the baseline file: use `# noqa: <rule>` (ruff), `// eslint-disable-next-line` (ESLint), or `# nosemgrep: <rule-id>` (Semgrep).

---

## What is the validator (Jiminy Cricket)?

At `thorough` and `paranoid` depth, tailtest runs a reasoning subagent called Jiminy Cricket after the normal test + security pass. Jiminy reads the diff Claude just made and asks: is there a logical error here? A missing edge case? An unsafe assumption? It emits `validator` findings when it spots something worth flagging.

Unlike lint rules, Jiminy's findings are reasoning-based -- it can catch things that no static rule would catch, like "this function silently returns None on the error path and the caller doesn't check." The tradeoff is that it makes an LLM call (costs money, adds latency) and can occasionally produce false positives.

Jiminy is enabled at `thorough` and `paranoid` depth. It is off at `standard` and below.

---

## What is the red team?

At `paranoid` depth, on projects where the scanner has classified `ai_surface: agent`, tailtest runs 64 LLM-based attack probes against the code. The probes cover prompt injection, data exfiltration paths, tool-call forgery, and context poisoning -- the attack categories most relevant to AI agent code.

Each probe is a targeted `claude -p` call that tries to elicit a specific unsafe behavior from the code under test. A probe that succeeds (the attack lands) becomes a `redteam` finding.

Red team only fires on agent-classified projects at `paranoid` depth. It never fires on `standard` or `thorough`, and it never fires on non-agent projects even at `paranoid`.

See [redteam-disclosure.md](redteam-disclosure.md) for the coordinated disclosure policy that applies when the red team finds a vulnerability in third-party code.

---

## What does `likely_vibe_coded` mean?

`likely_vibe_coded` is a heuristic flag the project scanner sets when it detects that more than 50% of the code in the project was likely written by an AI assistant AND the test suite is sparse relative to the codebase size.

The flag does not change what tailtest runs. It surfaces in `/tailtest:scan` output and in recommendations, and it adjusts the threshold at which tailtest offers `/tailtest:gen` suggestions -- vibe-coded projects get more aggressive test-generation nudges because the coverage gap is typically larger.

The heuristic looks at commit metadata, Claude Code session markers, and the ratio of test lines to source lines. It is intentionally imprecise; treat it as a rough signal, not a verdict.
