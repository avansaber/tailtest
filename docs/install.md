# Install + upgrade

This page covers the full install story, the upgrade path from older releases, and the known gotchas. For a 5-minute happy path, see [quickstart.md](quickstart.md) instead.

## TL;DR

```bash
# 1. Install the Python package (the hook needs it)
python3 -m python3 -m pip install tailtester

# 2. Install the Claude Code plugin
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber-tailtest

# 3. Restart Claude Code
```

That's it for most users on a fresh setup. The rest of this page covers the cases where it isn't.

## What gets installed

The install has two parts that serve different purposes:

**`python3 -m python3 -m pip install tailtester`** puts the tailtest engine (the Python package) on your PATH. The hook shim needs this because it runs `import tailtest.hook` when Claude Code fires the PostToolUse event. Without the pip install, the plugin installs but the hook fails silently because the Python import doesn't resolve. The Level 2 dogfood validated this: on a clean machine with only the plugin installed (no pip), the hook bootstrap tried to import tailtest, failed, and exited silently with a helpful stderr message.

**`claude plugin install tailtest@avansaber-tailtest`** does three things:

1. **Clones the tailtest plugin tree** into Claude Code's user-scope plugin directory (typically `~/.config/claude/plugins/`).
2. **Registers the hooks** (`PostToolUse`, `SessionStart`) by writing to your Claude Code hook configuration.
3. **Registers the skills** (`/tailtest:status`, `/tailtest:report`, etc.) into your Claude Code skill registry.

## Restart Claude Code after install

This is mandatory and the most common reason a fresh install doesn't work.

Claude Code freezes its hook registry and skill registry at session start. A plugin installed mid-session writes to disk but doesn't take effect until the next session. The fix is to quit any running Claude Code window completely and start a new one.

You'll know the restart worked when:

- `/tailtest:status` is recognized as a slash command in Claude Code
- The first edit you ask Claude to make produces a `tailtest: ...` line in the next-turn context

## macOS Homebrew Python: PEP 668

If you're on macOS with Homebrew Python and you want to use the standalone CLI (`python3 -m pip install tailtester`), you'll hit PEP 668's externally-managed-environment protection:

```
error: externally-managed-environment
```

Two options:

**Recommended: install via `pipx`** (isolates tailtest in its own venv, doesn't touch system Python):

```bash
brew install pipx
pipx install tailtester
```

After this, the `tailtest` command is on your PATH and runs against an isolated venv.

**Quick fix: `--break-system-packages`** (NOT recommended for shared environments):

```bash
python3 -m pip install --break-system-packages tailtester
```

This installs tailtest into your homebrew Python's site-packages. Works, but pollutes the global Python and is fragile across `brew upgrade`.

The plugin path (the recommended path for most users) does NOT need pip at all. PEP 668 only matters if you want the standalone CLI.

## Upgrading from v1 (`tailtester` 0.2.x or 0.3.x)

If you have the v1 `tailtester` package installed (the pre-rebuild version with `tailtest scan`, `tailtest run`, `tailtest doctor`), uninstall it BEFORE installing v2:

```bash
python3 -m pip uninstall tailtester
# or
pipx uninstall tailtester
# or
pip uninstall --break-system-packages tailtester
```

Why: the v1 and v2 packages share the `tailtest.hook` import path. If both are installed, v1 shadows v2 in `sys.path` and the v2 hook code never runs. The symptom is "the hook fires but no findings ever appear" because Claude Code is invoking v1 code that doesn't know about Phase 2 features.

After uninstalling v1, install v2 as described above.

## Upgrading the plugin itself

```bash
claude plugin upgrade tailtest@avansaber-tailtest
# then quit and restart Claude Code
```

Same restart-after-upgrade rule applies. Claude Code's hook + skill registries don't hot-reload on plugin upgrade.

## Uninstalling

```bash
claude plugin uninstall tailtest@avansaber-tailtest
# optionally:
rm -rf .tailtest/  # in any project that has tailtest state
```

This removes the hooks, skills, and plugin tree. Project-local `.tailtest/` directories (config, baseline, cache, reports) stay until you delete them manually. They're project state, not plugin state.

## Standalone CLI install

If you want to run tailtest outside Claude Code (CI pipelines, raw terminal use, an MCP-aware IDE that isn't Claude Code), install the Python package directly:

```bash
pipx install tailtester
# or, on a vanilla Python:
python3 -m pip install tailtester
```

After install, the `tailtest` command is on your PATH:

```bash
tailtest scan        # shallow project scan
tailtest run         # run impacted tests
tailtest doctor      # diagnose install issues
tailtest mcp-serve   # run as an MCP server
```

The PyPI package name is `tailtester` (a historical squat that we kept for continuity); the importable Python package is `tailtest`.

## Hook Python resolution

The shipped hook shim at `hooks/post_tool_use.py` starts with `#!/usr/bin/env python3`. Claude Code invokes this shim with whatever `python3` is first on the PATH at the time the hook fires.

This works in most environments. It can fail in two cases:

1. **Multiple Python interpreters on PATH.** If `/usr/bin/python3` and `/opt/homebrew/bin/python3` both exist and tailtest was pip-installed into the homebrew Python, but `/usr/bin/python3` is earlier on PATH, the shim runs against the system Python and `import tailtest.hook` fails. The symptom is the hook firing but producing no output.
2. **Project venv-only install.** If tailtest is installed into a project's `.venv/` and not globally, the shim has no way to know about the project venv (it's a Claude Code hook, not a project script). The shim runs against whatever PATH-resolved python3 is available, which won't have tailtest.

**The reliable fix is to install tailtest into the same Python interpreter that PATH resolves to** when the hook fires. For most users that means `pipx install tailtester` (which puts tailtest in an isolated venv but exposes the `tailtest` command on PATH and uses a stable Python).

A more robust shim that detects the right interpreter at install time is on the Phase 7 Task 7.4a roadmap. For alpha.2, the documentation-only workaround above is the recommended path.

## Skills don't hot-load on plugin swap

Same root cause as the restart-after-install rule. If you remove tailtest and install a different version mid-session, the skill registry doesn't pick up the new version. Restart Claude Code after any plugin install/upgrade/uninstall.

## SessionStart hook doesn't fire retroactively on plugin swap

If you install tailtest into a project that's already open in Claude Code, the `SessionStart` hook doesn't fire retroactively. This means `.tailtest/config.yaml` and `.tailtest/profile.json` won't be bootstrapped until the next session. Restart Claude Code (you knew this was coming).

The first edit you make in the new session will trigger the `PostToolUse` hook, which calls the scanner directly. The `SessionStart` hook ALSO fires when the new session starts, populating the project profile. Both paths produce useful state on the first edit; the SessionStart path is just more efficient because it pre-populates the cache.

## Verifying the install

Once you've installed and restarted, verify the install works:

```
/tailtest:status
```

If the slash command is recognized and produces output (even an "I haven't run yet" message), the plugin install path is working. If the command isn't recognized at all, the install or restart didn't take effect.

For a deeper diagnostic:

```bash
# Outside Claude Code, in a terminal:
tailtest doctor
```

`tailtest doctor` checks that the package is importable, prints the resolved interpreter path, prints the resolved tailtest version, and surfaces any obvious environment issues.

## Known install gotchas (Phase 7 Task 7.4a)

These are documented in detail above; this is a quick checklist for support purposes:

1. **Restart after install/upgrade** — Claude Code freezes hook + skill registries at session start
2. **Uninstall v1 first** when upgrading from `tailtester` 0.2.x or 0.3.x
3. **macOS + Homebrew Python** needs `pipx` or `--break-system-packages` for the standalone CLI path (not the plugin path)
4. **Hook Python resolution** — install into the python3 that's first on PATH; pipx is the most reliable path
5. **`claude plugin install --from-path` doesn't exist** as a CLI verb; use `claude plugin marketplace add <repo>` then `claude plugin install <plugin>@<repo>`
6. **`SessionStart` hook doesn't fire retroactively** on mid-session plugin install; restart picks it up

If you hit one of these and the docs above don't unblock you, the [tailtest issue tracker](https://github.com/avansaber/tailtest/issues) is the right place to ask.
