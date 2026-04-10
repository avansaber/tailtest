"""Shared bootstrap for tailtest hook shims (Phase 7 Task 7.4a).

When Claude Code invokes a tailtest hook, the shim runs against
whatever ``python3`` is first on PATH at hook-fire time. That
interpreter may not be the one tailtest is installed into, in
which case ``import tailtest.hook`` fails and the hot loop dies
silently. The parallel Level 2 dogfood (2026-04-09) caught the
exact symptom: hook fires, no findings appear, no log line, no
way to distinguish "hook crashed" from "hook not installed".

This module fixes the symptom by:

1. Trying the import directly. If it works, the shim continues.
2. If it fails, looking up the ``tailtest`` CLI on PATH (which
   pipx and pip install put alongside the python interpreter
   that owns it) and reading its shebang line to find the
   interpreter that has tailtest installed. Re-execs the
   current shim with that interpreter so the import succeeds.
3. Guarding re-exec with the ``TAILTEST_HOOK_REEXEC`` env var
   so a misbehaving install can't cause an exec loop.
4. On final failure, writing a clear error to stderr (which
   Claude Code's ``--debug`` log captures) and exiting 0 so
   the hot loop never blocks Claude's next turn.

The helpers are small and pure where possible so they can be
unit-tested via the test suite without spawning subprocesses.
"""

from __future__ import annotations

import os
import shutil
import sys

# Re-exec guard env var. Set when bootstrap_or_die calls execl
# so the new process doesn't bounce into another re-exec on its
# own import failure (which would loop forever).
_REEXEC_ENV_VAR = "TAILTEST_HOOK_REEXEC"

_INSTALL_DOC_URL = "https://github.com/avansaber/tailtest/blob/main/docs/install.md"


def can_import_tailtest_hook() -> bool:
    """Return True iff ``import tailtest.hook`` succeeds in this process.

    Used by ``bootstrap_or_die`` to decide whether a re-exec is
    necessary. Imports lazily so callers don't pay the cost when
    the bootstrap path is not needed.
    """
    try:
        import tailtest.hook  # noqa: F401
    except ImportError:
        return False
    return True


def find_tailtest_python() -> str | None:
    """Find the python interpreter that has tailtest installed.

    Looks up the ``tailtest`` CLI on PATH and reads its shebang
    line. pipx, pip, and conda all install console_scripts as
    small wrappers whose first line is the interpreter path
    (e.g., ``#!/Users/x/.local/pipx/venvs/tailtester/bin/python``).
    Returns None when:

    - ``tailtest`` is not on PATH
    - the file's first line is not a shebang
    - the shebang points at ``/usr/bin/env <name>`` (the case we
      are trying to escape from; following it would loop)
    - the resolved interpreter path does not exist on disk
    """
    tailtest_bin = shutil.which("tailtest")
    if tailtest_bin is None:
        return None
    try:
        with open(tailtest_bin, encoding="utf-8") as f:
            first_line = f.readline().strip()
    except OSError:
        return None
    if not first_line.startswith("#!"):
        return None
    interpreter_line = first_line[2:].strip()
    # Don't follow `#!/usr/bin/env python3` shebangs. That is
    # the exact case we're trying to escape: a shebang that
    # delegates back to PATH lookup. Following it would re-exec
    # against the same broken python.
    if interpreter_line.startswith("/usr/bin/env "):
        return None
    # Some shebangs include args after the interpreter path
    # (e.g., `/usr/bin/python3 -E`). Take only the path token.
    interpreter = interpreter_line.split(None, 1)[0]
    if not os.path.isfile(interpreter):
        return None
    return interpreter


def reexec_with(interpreter: str, script: str) -> None:
    """Re-exec the current script with a different Python interpreter.

    Sets ``TAILTEST_HOOK_REEXEC=1`` so the new process won't
    bounce into another re-exec on its own import failure.
    Never returns on success because ``os.execl`` replaces the
    current process. Raises on ``os.execl`` failure (rare).
    """
    os.environ[_REEXEC_ENV_VAR] = "1"
    os.execl(interpreter, interpreter, script)


def bootstrap_or_die(script_path: str) -> None:
    """Ensure ``tailtest.hook`` is importable in this process.

    If the import works, returns immediately. If not, tries one
    re-exec with the interpreter that has tailtest installed.
    If both paths fail, writes a clear error to stderr and
    raises ``SystemExit(0)`` so the hot loop doesn't block
    Claude's next turn.

    The shim should call this BEFORE importing anything from
    ``tailtest.hook``. Pass ``__file__`` as the script_path so
    the re-exec runs the same shim file under the new
    interpreter.
    """
    if can_import_tailtest_hook():
        return

    if os.environ.get(_REEXEC_ENV_VAR) == "1":
        # We already tried the re-exec; the target interpreter
        # also can't import tailtest. Give up cleanly.
        sys.stderr.write(
            "tailtest hook: cannot import `tailtest.hook` even after "
            "re-exec via the tailtest CLI's python interpreter. "
            "Install tailtest into the same python that runs the "
            "hook (recommended: `pipx install tailtester`). "
            f"See {_INSTALL_DOC_URL} for the full install + upgrade "
            "story.\n"
        )
        raise SystemExit(0)

    interpreter = find_tailtest_python()
    if interpreter is None:
        sys.stderr.write(
            "tailtest hook: cannot import `tailtest.hook` and no "
            "`tailtest` CLI found on PATH. Install tailtest with "
            "`pipx install tailtester` (recommended) or `pip install "
            "tailtester` into the python interpreter that runs the "
            f"hook. See {_INSTALL_DOC_URL}\n"
        )
        raise SystemExit(0)

    # Re-exec replaces the current process; on success this call
    # never returns. On failure (rare; OSError from execl) the
    # exception propagates to the shim's main() which has its
    # own broad except handler.
    reexec_with(interpreter, script_path)
