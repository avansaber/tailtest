"""tailtest.hook, plugin hook runtime logic.

Phase 1 Tasks 1.5 (PostToolUse), 1.6 (SessionStart). The actual
executable entry points live at `hooks/<name>.py` in the repo root,
per the Claude Code plugin contract (the plugin manifest invokes
them as subprocesses). Those files are thin shims that import this
package and delegate to the async run functions here.

Keeping the logic in an importable Python package lets unit tests
exercise the hook without spawning a real subprocess.
"""

__all__: list[str] = []
