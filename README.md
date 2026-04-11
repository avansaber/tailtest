# tailtest

> The test + security validator that lives inside Claude Code. Never blocks, never lies.

tailtest watches every edit your AI agent makes inside Claude Code, runs the tests that matter, scans for security issues that matter, and feeds findings back into Claude's next turn so the agent can fix them in the same session. The hot loop never blocks your work.

**Current release:** [v0.1.1](https://github.com/avansaber/tailtest/releases/tag/v0.1.1) — Phase 7 (launch) shipped. Tests + 4 security scanners (secrets, SAST, SCA, AI red team) in the Claude Code hot loop.

## Quickstart (5 minutes)

```bash
# 1. Install the plugin from the GitHub marketplace (no pip needed)
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber

# 2. Restart your Claude Code session
# (the skill registry doesn't hot-load, so a restart is mandatory)

# 3. Open a project and let Claude edit a Python or JS/TS file
# tailtest's hot loop fires automatically on every Edit/Write
```

After the first edit, look for `tailtest: N/N tests passed · M.Ms` in Claude's next-turn context. That is the hot loop talking to Claude. The full output lands at `.tailtest/reports/latest.html` and `.tailtest/reports/latest.json`.

**Want the standalone CLI too?** `pip install tailtester` gives you `tailtest run`, `tailtest doctor`, and `tailtest scan` outside of Claude Code. It is optional for plugin users.

For the full walkthrough including troubleshooting, see [`docs/quickstart.md`](docs/quickstart.md). For install gotchas (PEP 668, v1 upgrade, hook Python resolution), see [`docs/install.md`](docs/install.md). For the full config schema, see [`docs/configuration.md`](docs/configuration.md).

## What tailtest does today

- **Runs your tests on every edit**, using the project's native runner (pytest for Python; vitest or jest for JS/TS) with native test impact analysis so only the affected tests run.
- **Scans for secrets** via gitleaks on every changed file. CWE-798 (hardcoded credentials) tagged automatically.
- **Scans for SAST issues** via Semgrep across the changed files using the curated `p/default` ruleset. Configurable per project.
- **Scans dependencies** for known vulnerabilities via the OSV.dev API on every manifest edit (`pyproject.toml`, `package.json`). Hydrated severity, CWE IDs, fixed-version hints, and alias dedup so you see each advisory once.
- **Computes delta coverage** on the lines your edit touched (Python). The next-turn context calls out exactly which new lines are uncovered.
- **Suggests test generation** for pure functions you just added that have no test, so you can run `/tailtest:gen <file>` and let Claude write a starter test for you.
- **Filters known issues via a baseline** at `.tailtest/baseline.yaml`. Existing debt stays silent; only NEW issues surface.
- **Renders a self-contained HTML report** at `.tailtest/reports/latest.html` after every run. No JavaScript, no CDN, opens offline.
- **Red-teams AI agent entry points** at `paranoid` depth via a 64-attack catalog covering the OWASP LLM Top 10. Each attack is judged by `claude -p`; findings land in a timestamped HTML report at `.tailtest/reports/redteam-*.html`.
- **Never blocks your work.** tailtest reports; you decide.

## Slash commands

After install, Claude Code knows the following user-invocable skills:

| Command | What it does |
|---|---|
| `/tailtest:status` | Compact one-screen status: depth, runner, last-run summary, delta coverage, next action |
| `/tailtest:report` | Full detail of the last run; opens the path to `latest.html` so you can read it in a browser |
| `/tailtest:security` | Current security posture: which scanners are on, ruleset, new vs baselined finding counts |
| `/tailtest:debt` | Review the accepted-debt baseline (`.tailtest/baseline.yaml`) |
| `/tailtest:scan` | Re-scan the project profile from scratch |
| `/tailtest:gen` | Generate a starter test for an uncovered function |
| `/tailtest:depth` | Change the hot loop depth (`off`/`quick`/`standard`/`thorough`/`paranoid`) |
| `/tailtest:setup` | Onboarding interview that writes `.tailtest/config.yaml` |
| `/tailtest:memory` | View or clear the validator memory file (`.tailtest/memory/validator.md`) |
| `/tailtest:help` | Lists all skills with descriptions and links to docs |
| `/tailtest` | Show active project recommendations; dismiss or accept with an ID |

## Configuration

Optional. tailtest works out of the box with sensible defaults. To customize, drop a `.tailtest/config.yaml` in your project root:

```yaml
schema_version: 1
depth: standard          # off | quick | standard | thorough | paranoid

security:
  secrets: true          # gitleaks
  sast:
    enabled: true
    ruleset: p/default   # any Semgrep ruleset id, including p/owasp-top-ten or p/ci
  sca:
    enabled: true
    use_epss: false      # opt-in to EPSS scoring (off until EPSS.io integration ships)
  block_on_verified_secret: false

notifications:
  auto_offer_generation: true   # offer /tailtest:gen suggestions in the hot loop
```

Phase 1 configs (with `sast: true/false` as plain bools) keep parsing — the loader migrates them to the nested form transparently.

## Distribution channels

| Channel | Install | When to use it |
|---|---|---|
| **Claude Code plugin** (recommended) | `claude plugin marketplace add avansaber/tailtest && claude plugin install tailtest@avansaber` | Full experience: hot loop hooks, skills, MCP, on-disk output. No pip required. |
| **Standalone CLI** (optional) | `pip install tailtester` | CI pipelines, raw terminal use, or any workflow outside Claude Code. The PyPI package name is `tailtester`; the importable Python package is `tailtest`. |
| **MCP server** | `tailtest mcp-serve` | Cursor, Windsurf, Codex, or any MCP-aware IDE. Requires the pip install. Phase 4 will harden this path. |

## Install notes

- **pip is optional for plugin users.** The plugin is self-contained; the bootstrap finds the tailtest engine inside the plugin tree. You only need `pip install tailtester` if you want the standalone CLI or MCP server outside Claude Code.
- **Restart Claude Code after installing or upgrading the plugin.** The skill registry and hook registry are frozen at session start; new plugins aren't picked up mid-session.
- **macOS + Homebrew Python users:** if you do install the standalone CLI, PEP 668 means `pip install tailtester` needs `--break-system-packages` or (better) `pipx install tailtester` to keep tailtest in its own venv.
- **Upgrading from v1 (`tailtester` 0.2.x)?** Uninstall the v1 package first (`pip uninstall tailtester`). The v1 and v2 packages share the `tailtest.hook` import path; without the uninstall step, v1 shadows v2.

## What tailtest does NOT do (yet)

Current limitations in v0.1.1:

- No multi-language SCA beyond Python + JS. Go / Rust / Java on the roadmap.
- No EPSS / KEV / NVD severity enrichment for SCA findings.
- No SCA discovery for projects without a `pyproject.toml` or `package.json`.

## Repository layout

```
tailtest/
├── .claude-plugin/plugin.json   Claude Code plugin manifest
├── .mcp.json                    MCP server wiring
├── hooks/                       PostToolUse / SessionStart hook shims
├── skills/                      User-invocable slash commands
├── src/tailtest/                Python package source
├── tests/                       Pytest suite (1092 tests)
├── pyproject.toml
├── README.md                    This file
├── LICENSE                      Apache 2.0
├── CHANGELOG.md
└── SECURITY.md
```

## Contributing

The project is in active development through Phase 7 (launch). The current release is `v0.1.1`. The issue tracker on [github.com/avansaber/tailtest](https://github.com/avansaber/tailtest) is open for bug reports and feature requests. PRs are reviewed but the bar for accepting external code is high while the architecture is still moving.

## License

[Apache 2.0](LICENSE). Copyright 2026 AvanSaber Inc.

## Security

See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability. tailtest practices what it preaches: every release passes a hygiene audit (gitleaks, trufflehog, manual review) before it ships, and the security layer dogfoods itself by scanning the public source tree on every commit.
