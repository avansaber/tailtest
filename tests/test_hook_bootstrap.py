"""Tests for ``hooks/_bootstrap.py`` (Phase 7 Task 7.4a).

The bootstrap helper resolves the right Python interpreter for
the hook shim when Claude Code invokes the shim with a python
that doesn't have ``tailtest`` installed. The helper is small
and pure where possible so the tests can exercise it via direct
function calls + monkeypatch instead of subprocess gymnastics.

The actual ``os.execl`` re-exec path is tested by mocking
``os.execl`` (which would otherwise replace the test process).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# The hooks/ directory is not part of the importable tailtest
# package; it's a sibling directory at repo root containing the
# Claude Code shim scripts. Load `_bootstrap.py` directly via
# importlib.util so the test suite can exercise it without
# polluting sys.path.
_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
_BOOTSTRAP_PATH = _HOOKS_DIR / "_bootstrap.py"


def _load_bootstrap_module():
    """Import hooks/_bootstrap.py as a fresh module per test.

    Each call returns a new module instance so monkeypatched
    state from one test doesn't bleed into another.
    """
    spec = importlib.util.spec_from_file_location("tailtest_test_hook_bootstrap", _BOOTSTRAP_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- can_import_tailtest_hook ----------------------------------------


def test_can_import_returns_true_in_test_process() -> None:
    """In the pytest process, tailtest IS installed, so the import works."""
    bootstrap = _load_bootstrap_module()
    assert bootstrap.can_import_tailtest_hook() is True


def test_can_import_returns_false_on_import_error(monkeypatch) -> None:
    """Force the import to raise; helper returns False, not raises."""
    bootstrap = _load_bootstrap_module()

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "tailtest.hook":
            raise ImportError("simulated missing tailtest")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert bootstrap.can_import_tailtest_hook() is False


# --- find_tailtest_python --------------------------------------------


def test_find_tailtest_python_returns_none_when_not_on_path(
    monkeypatch,
) -> None:
    """No `tailtest` CLI on PATH means we cannot bootstrap."""
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: None)
    assert bootstrap.find_tailtest_python() is None


def test_find_tailtest_python_reads_shebang(monkeypatch, tmp_path: Path) -> None:
    """A pipx-style CLI script: shebang points at a real interpreter."""
    bootstrap = _load_bootstrap_module()
    fake_python = tmp_path / "venv" / "bin" / "python3.13"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("# pretend python interpreter")
    fake_python.chmod(0o755)

    fake_cli = tmp_path / "tailtest"
    fake_cli.write_text(
        f"#!{fake_python}\n# pipx-installed CLI wrapper\nfrom tailtest.cli import main\nmain()\n"
    )

    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(fake_cli))
    result = bootstrap.find_tailtest_python()
    assert result == str(fake_python)


def test_find_tailtest_python_skips_env_shebang(monkeypatch, tmp_path: Path) -> None:
    """A `#!/usr/bin/env python3` shebang is the case we're escaping;
    don't follow it because it would re-exec against the same broken
    PATH-resolved python."""
    bootstrap = _load_bootstrap_module()
    fake_cli = tmp_path / "tailtest"
    fake_cli.write_text("#!/usr/bin/env python3\nfrom tailtest.cli import main\n")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(fake_cli))
    assert bootstrap.find_tailtest_python() is None


def test_find_tailtest_python_handles_shebang_with_args(monkeypatch, tmp_path: Path) -> None:
    """A shebang like `#!/usr/bin/python3 -E` should yield just the
    interpreter path, not the args."""
    bootstrap = _load_bootstrap_module()
    fake_python = tmp_path / "python3"
    fake_python.write_text("# fake")
    fake_python.chmod(0o755)

    fake_cli = tmp_path / "tailtest"
    fake_cli.write_text(f"#!{fake_python} -E\n")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(fake_cli))
    result = bootstrap.find_tailtest_python()
    assert result == str(fake_python)


def test_find_tailtest_python_returns_none_when_interpreter_does_not_exist(
    monkeypatch, tmp_path: Path
) -> None:
    """Shebang points at a path that no longer exists (stale install)."""
    bootstrap = _load_bootstrap_module()
    fake_cli = tmp_path / "tailtest"
    fake_cli.write_text("#!/path/that/does/not/exist/python\n")

    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(fake_cli))
    assert bootstrap.find_tailtest_python() is None


def test_find_tailtest_python_returns_none_when_no_shebang(monkeypatch, tmp_path: Path) -> None:
    fake_cli = tmp_path / "tailtest"
    fake_cli.write_text("from tailtest.cli import main\nmain()\n")

    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(fake_cli))
    assert bootstrap.find_tailtest_python() is None


def test_find_tailtest_python_returns_none_on_unreadable_file(monkeypatch, tmp_path: Path) -> None:
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _name: str(tmp_path / "nonexistent"))
    # The file referenced by `which` doesn't actually exist; open() raises.
    assert bootstrap.find_tailtest_python() is None


# --- bootstrap_or_die: success path ----------------------------------


def test_bootstrap_or_die_returns_silently_when_import_works(
    monkeypatch,
) -> None:
    """When tailtest is importable, bootstrap is a no-op (no re-exec, no exit)."""
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap, "can_import_tailtest_hook", lambda: True)

    # Track that nothing tried to re-exec.
    call_log: list[str] = []
    monkeypatch.setattr(bootstrap, "find_tailtest_python", lambda: call_log.append("find") or None)
    monkeypatch.setattr(bootstrap.os, "execl", lambda *a, **k: call_log.append("execl"))

    bootstrap.bootstrap_or_die("/tmp/whatever.py")
    assert call_log == []


# --- bootstrap_or_die: re-exec path ----------------------------------


def test_bootstrap_or_die_reexecs_when_import_fails(monkeypatch, tmp_path: Path) -> None:
    """Import fails, find succeeds, re-exec happens with the right args."""
    bootstrap = _load_bootstrap_module()

    monkeypatch.setattr(bootstrap, "can_import_tailtest_hook", lambda: False)
    monkeypatch.setattr(bootstrap, "find_tailtest_python", lambda: "/fake/venv/bin/python3")
    monkeypatch.delenv("TAILTEST_HOOK_REEXEC", raising=False)

    execl_calls: list[tuple] = []

    def fake_execl(*args):
        execl_calls.append(args)
        # Simulate execl returning instead of replacing the process so
        # the test can finish. The real execl never returns.

    monkeypatch.setattr(bootstrap.os, "execl", fake_execl)

    bootstrap.bootstrap_or_die("/tmp/shim.py")

    assert len(execl_calls) == 1
    interpreter, _arg0, script = execl_calls[0]
    assert interpreter == "/fake/venv/bin/python3"
    assert script == "/tmp/shim.py"
    # Re-exec guard env var was set.
    assert os.environ.get("TAILTEST_HOOK_REEXEC") == "1"


def test_bootstrap_or_die_exits_when_import_fails_and_no_python_found(monkeypatch, capsys) -> None:
    """Import fails, find returns None: print stderr message + exit 0."""
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap, "can_import_tailtest_hook", lambda: False)
    monkeypatch.setattr(bootstrap, "find_tailtest_python", lambda: None)
    monkeypatch.delenv("TAILTEST_HOOK_REEXEC", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        bootstrap.bootstrap_or_die("/tmp/shim.py")
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    assert "tailtest hook" in captured.err
    assert "no `tailtest` CLI found on PATH" in captured.err
    assert "pipx install tailtester" in captured.err


def test_bootstrap_or_die_exits_when_reexec_already_attempted(monkeypatch, capsys) -> None:
    """The re-exec guard fires: print stderr + exit 0, never call execl again."""
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap, "can_import_tailtest_hook", lambda: False)
    monkeypatch.setenv("TAILTEST_HOOK_REEXEC", "1")

    execl_calls: list[tuple] = []
    monkeypatch.setattr(bootstrap.os, "execl", lambda *a: execl_calls.append(a))

    with pytest.raises(SystemExit) as exc_info:
        bootstrap.bootstrap_or_die("/tmp/shim.py")
    assert exc_info.value.code == 0
    assert execl_calls == []

    captured = capsys.readouterr()
    assert "tailtest hook" in captured.err
    assert "even after re-exec" in captured.err


def test_bootstrap_or_die_messages_link_to_install_doc(monkeypatch, capsys) -> None:
    """Both stderr error paths point users at the install doc."""
    bootstrap = _load_bootstrap_module()
    monkeypatch.setattr(bootstrap, "can_import_tailtest_hook", lambda: False)
    monkeypatch.setattr(bootstrap, "find_tailtest_python", lambda: None)
    monkeypatch.delenv("TAILTEST_HOOK_REEXEC", raising=False)

    with pytest.raises(SystemExit):
        bootstrap.bootstrap_or_die("/tmp/shim.py")

    captured = capsys.readouterr()
    assert "docs/install.md" in captured.err


# --- regression: shim files import _bootstrap correctly ---------------


def test_post_tool_use_shim_imports_bootstrap() -> None:
    """The PostToolUse shim must reference _bootstrap so the bootstrap
    path is wired up. Quick string-search regression so a future
    refactor cannot accidentally remove the bootstrap call."""
    shim = (_HOOKS_DIR / "post_tool_use.py").read_text(encoding="utf-8")
    assert "from _bootstrap import bootstrap_or_die" in shim
    assert "bootstrap_or_die(__file__)" in shim


def test_session_start_shim_imports_bootstrap() -> None:
    """Same regression check for the SessionStart shim."""
    shim = (_HOOKS_DIR / "session_start.py").read_text(encoding="utf-8")
    assert "from _bootstrap import bootstrap_or_die" in shim
    assert "bootstrap_or_die(__file__)" in shim
