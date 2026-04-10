# Quickstart

Get tailtest running in your project in 5 minutes.

This guide assumes you already have [Claude Code](https://claude.com/claude-code) installed and a Python or JavaScript/TypeScript project ready to work in. If you don't have a project, any pytest or vitest project will do; tailtest works on existing projects without modification.

## 1. Install the Python package + plugin

```bash
# The hook needs the tailtest Python package on PATH
python3 -m pip install tailtester

# Then install the Claude Code plugin from the GitHub marketplace
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber-tailtest
```

The `pip install` step puts the `tailtest` engine on your PATH so the hook shim can import it. The `marketplace add` + `plugin install` steps register the hooks and skills into Claude Code. Both are reversible (`python3 -m pip uninstall tailtester` + `claude plugin uninstall tailtest@avansaber-tailtest`).

If you hit install errors on macOS Homebrew Python or you're upgrading from the v1 `tailtester` package, see [install.md](install.md) for the full details.

## 2. Restart Claude Code

```bash
# Quit any running Claude Code session and start a new one.
```

This step is mandatory. Claude Code freezes its hook registry and skill registry at session start, so a plugin installed mid-session won't fire on the next edit. A fresh session picks up the new plugin cleanly.

## 3. Open your project

```bash
cd /path/to/your/project
claude
```

Once Claude Code starts, tailtest's `SessionStart` hook fires, runs a shallow project scan, and writes the result to `.tailtest/profile.json`. This is silent unless something is wrong.

## 4. Make an edit

Ask Claude to do anything that involves editing a file:

> Edit `src/util.py` to add a `multiply(a, b)` function.

The moment Claude finishes the edit, tailtest's `PostToolUse` hook fires:

1. The hook detects which tests are impacted by the change (via pytest-testmon for Python, vitest related / jest --findRelatedTests for JS/TS).
2. It runs only the impacted tests (not the whole suite).
3. It scans the changed files for secrets via gitleaks.
4. It scans the changed files for SAST issues via Semgrep (if installed).
5. If you edited `pyproject.toml` or `package.json`, it scans the dep diff via OSV.
6. It merges all findings into one batch, applies the baseline filter, and feeds a compact summary into Claude's next-turn context.

You should see a one-line summary like this in Claude's reply:

```
tailtest: 14/14 tests passed · 1 new security issue · 1.8s
```

If the line is missing entirely, see the [Troubleshooting](#troubleshooting) section below.

## 5. Open the report

After every run, tailtest writes a self-contained HTML report to `.tailtest/reports/latest.html`:

```bash
open .tailtest/reports/latest.html
```

Or invoke the `/tailtest:report` skill from inside Claude Code:

```
/tailtest:report
```

The skill will read both `latest.html` and `latest.json`, print the absolute path so you can open it in a browser, and produce an inline summary in the conversation.

## 6. Inspect your security posture

Once you have one run under your belt, ask tailtest about its scanner posture:

```
/tailtest:security
```

You'll see which scanners are enabled, the active Semgrep ruleset, the count of new findings, and the count of findings already in the baseline (if any).

If you want to see the baseline itself:

```
/tailtest:debt
```

## What just happened

Tailtest is a Claude Code plugin that watches every edit your AI agent makes, runs the tests that matter, scans for security issues that matter, and feeds findings back into the conversation so the agent can self-correct in the same session. The hot loop never blocks your work and never modifies your code.

You did not have to write any configuration. Tailtest works out of the box with sensible defaults: `depth: standard`, all three scanners enabled, baseline auto-populated on the first green run.

## Customize (optional)

If you want to change the depth, pin a different Semgrep ruleset, or disable a scanner, drop a `.tailtest/config.yaml` in your project root. See [configuration.md](configuration.md) for the full schema.

The fastest way to get a working config is the onboarding interview:

```
/tailtest:setup
```

This walks you through 3-4 questions and writes the config file for you.

## What if a test fails?

Tailtest reports failures into Claude's next turn but never blocks. If a test fails, Claude sees the failure in its next-turn context and can fix it in the same session if you ask. The hot loop waits for tests to pass before running the security phase, so you'll never see security findings buried under test noise.

If a test failure is intentional (you're mid-refactor and the suite is temporarily red), tailtest's baseline manager will pick up the failure after 3 consecutive failing runs and silence it. You can review what's been baselined with `/tailtest:debt`.

## Troubleshooting

**The hot loop summary line never appears.**

Either tailtest isn't running, or it ran and produced no output. Check `.tailtest/reports/latest.json` for the most recent run. If the file doesn't exist, tailtest hasn't fired at all; restart Claude Code. If the file exists but the additionalContext line is missing from the conversation, run with `claude --debug` and look for `tailtest` log lines.

**I see a "self-edit excluded" reason in the debug log.**

This is correct behavior when you're editing files inside the tailtest source tree itself. The hook deliberately skips its own source so tailtest never runs against itself.

**Tests fail with `ModuleNotFoundError` for a package the project depends on.**

Tailtest runs pytest with the same interpreter that pip-installed it. If the missing package is a project dependency, install it into your project's venv. If it's a tailtest dep, see [install.md](install.md) for the venv-resolution story.

**SCA findings come back at "info" severity.**

This was a real bug in alpha.1; alpha.2 fixes it via OSV per-vuln hydration. If you're on alpha.1, upgrade to alpha.2.

**The plugin install command does not exist.**

Make sure you're on Claude Code 2.x or newer and that the `gh` CLI is authenticated to a GitHub account. The plugin marketplace add command is `claude plugin marketplace add avansaber/tailtest` (no `--from-path`, no `--source`).

## Next steps

- [install.md](install.md) — full install + upgrade story, including the Phase 7 Task 7.4a gotchas
- [configuration.md](configuration.md) — `.tailtest/config.yaml` schema reference
- [`/tailtest:status`](../skills/status/SKILL.md) — compact project status anytime
- [`/tailtest:depth`](../skills/depth/SKILL.md) — change depth modes
- The [v0.1.0-alpha.2 release notes](https://github.com/avansaber/tailtest/releases/tag/v0.1.0-alpha.2) for the full feature list
