# tailtest docs

The test + security validator that lives inside Claude Code. Never blocks, never lies.

## Pages

| Page | What it covers |
|---|---|
| [quickstart.md](quickstart.md) | 5-minute install + first-run walkthrough |
| [install.md](install.md) | Full install, upgrade, and PEP 668 / v1-collision gotchas |
| [configuration.md](configuration.md) | Complete `.tailtest/config.yaml` schema reference |
| [depth-modes.md](depth-modes.md) | All five depth modes and when to use each |
| [findings-catalog.md](findings-catalog.md) | Every finding kind tailtest can emit, with examples |
| [faq.md](faq.md) | Common questions about pip, privacy, language support, and more |
| [redteam-disclosure.md](redteam-disclosure.md) | Coordinated disclosure policy for red-team findings against third-party code |
| [validator.md](validator.md) | Validator subagent (Jiminy Cricket): when it fires, finding severity, baselineing, memory |

## Slash commands

All commands are available inside Claude Code after the plugin is installed. Type `/tailtest:help` to see them inline.

| Command | What it does |
|---------|-------------|
| `/tailtest` | Show active recommendations; dismiss or accept with an ID |
| `/tailtest:status` | Compact status: depth, runner, last-run summary, delta coverage, next action |
| `/tailtest:report` | Full detail of the last run; opens `latest.html` path for browser view |
| `/tailtest:security` | Security posture: which scanners are on, ruleset, new vs baselined findings |
| `/tailtest:debt` | Review and manage the accepted-debt baseline (`.tailtest/baseline.yaml`) |
| `/tailtest:scan` | Re-scan the project profile from scratch; use `--deep` for LLM-backed scan |
| `/tailtest:gen <file>` | Generate a domain-aware starter test for a source file |
| `/tailtest:depth <mode>` | Change hot loop depth (`off` / `quick` / `standard` / `thorough` / `paranoid`) |
| `/tailtest:setup` | Onboarding interview that writes `.tailtest/config.yaml` |
| `/tailtest:memory` | View or clear the validator memory file (`.tailtest/memory/validator.md`) |
| `/tailtest:help` | List all skills with descriptions |

## Quick links

- [GitHub repo](https://github.com/avansaber/tailtest)
- [Changelog](../CHANGELOG.md)
- [Security policy](../SECURITY.md)
- [Apache 2.0 license](../LICENSE)
