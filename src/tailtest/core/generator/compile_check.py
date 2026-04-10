"""Per-language compile checks for the test generator (Phase 1 Task 1.12b).

Given a generated file path, run the cheapest possible check that the
file parses as valid code in its target language. If the check fails,
the caller deletes the file and surfaces the compiler output to the
user so they can decide whether to author the test by hand.

These checks are intentionally shallow:
- Python: ``ast.parse()`` (in-process, no subprocess)
- JavaScript: ``node --check`` (subprocess)
- TypeScript: ``npx tsc --noEmit`` (subprocess)

Deeper checks (import resolution, pytest collection, vitest collection)
are deferred to the caller or to a later phase. A syntactically valid
test file that fails at import time can still be a useful starting
point for a human reviewer.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CompileCheckResult:
    """Outcome of a single compile check.

    Attributes
    ----------
    ok:
        True if the file parsed cleanly.
    tool:
        The name of the tool used (``ast``, ``node``, ``tsc``, or
        ``skipped``). Callers may log this for diagnostic purposes.
    message:
        Empty on success, or the tool's stderr / exception message on
        failure, trimmed to the most useful prefix.
    """

    ok: bool
    tool: str
    message: str


async def check_python(path: Path) -> CompileCheckResult:
    """Syntax-check a Python file via ``ast.parse``.

    This is the fastest possible check and does not require any
    subprocess. Imports are NOT validated, that would require a full
    pytest collection pass against the target project's venv.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return CompileCheckResult(ok=False, tool="ast", message=f"could not read file: {exc}")
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        detail = f"line {exc.lineno}: {exc.msg}" if exc.lineno else exc.msg
        return CompileCheckResult(ok=False, tool="ast", message=detail)
    return CompileCheckResult(ok=True, tool="ast", message="")


async def check_javascript(path: Path) -> CompileCheckResult:
    """Syntax-check a JavaScript file via ``node --check``.

    Requires ``node`` on PATH. If node is missing the check returns
    ``ok=True`` with ``tool="skipped"`` rather than failing, since the
    absence of a runtime tool should not block generation.
    """
    if shutil.which("node") is None:
        return CompileCheckResult(ok=True, tool="skipped", message="node not on PATH")
    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            "--check",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=10)
    except (OSError, TimeoutError) as exc:
        return CompileCheckResult(ok=False, tool="node", message=str(exc))
    if (proc.returncode or 0) == 0:
        return CompileCheckResult(ok=True, tool="node", message="")
    stderr = stderr_bytes.decode(errors="replace").strip()
    stdout = stdout_bytes.decode(errors="replace").strip()
    return CompileCheckResult(
        ok=False,
        tool="node",
        message=(stderr or stdout or f"node --check exited {proc.returncode}")[:500],
    )


async def check_typescript(path: Path) -> CompileCheckResult:
    """Typecheck a TypeScript file via ``npx tsc --noEmit``.

    Requires ``npx`` on PATH. If npx is missing, falls back to a
    ``node --check`` equivalent (which catches at least obvious syntax
    errors even though it does not understand TypeScript syntax beyond
    what Node's parser tolerates). If both are missing, returns
    ``ok=True`` with ``tool="skipped"``.
    """
    if shutil.which("npx") is None:
        return await check_javascript(path)
    try:
        proc = await asyncio.create_subprocess_exec(
            "npx",
            "tsc",
            "--noEmit",
            "--target",
            "esnext",
            "--module",
            "esnext",
            "--moduleResolution",
            "node",
            "--strict",
            "false",
            "--allowJs",
            "--skipLibCheck",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (OSError, TimeoutError) as exc:
        return CompileCheckResult(ok=False, tool="tsc", message=str(exc))
    if (proc.returncode or 0) == 0:
        return CompileCheckResult(ok=True, tool="tsc", message="")
    stderr = stderr_bytes.decode(errors="replace").strip()
    stdout = stdout_bytes.decode(errors="replace").strip()
    return CompileCheckResult(
        ok=False,
        tool="tsc",
        message=(stderr or stdout or f"tsc exited {proc.returncode}")[:500],
    )


async def check_rust(path: Path) -> CompileCheckResult:
    """Check a Rust file via ``cargo check --tests``.

    Walks up from ``path`` to find the nearest ``Cargo.toml`` (the crate
    root), then runs ``cargo check --tests --quiet`` in that directory.
    This validates that the generated test code (colocated block appended
    to the source, or a new integration test file) at least compiles.

    Returns ``ok=True`` with ``tool="skipped"`` when ``cargo`` is not on
    PATH so the absence of the Rust toolchain does not block generation.
    """
    if shutil.which("cargo") is None:
        return CompileCheckResult(ok=True, tool="skipped", message="cargo not on PATH")

    # Walk up from the file to find the crate root.
    crate_root: Path | None = None
    current = path.parent if path.is_file() else path
    while True:
        if (current / "Cargo.toml").exists():
            crate_root = current
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    if crate_root is None:
        return CompileCheckResult(
            ok=True, tool="skipped", message="no Cargo.toml found; skipping compile check"
        )

    try:
        proc = await asyncio.create_subprocess_exec(
            "cargo",
            "check",
            "--tests",
            "--quiet",
            cwd=str(crate_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60)
    except (OSError, TimeoutError) as exc:
        return CompileCheckResult(ok=False, tool="cargo-check", message=str(exc))

    if (proc.returncode or 0) == 0:
        return CompileCheckResult(ok=True, tool="cargo-check", message="")
    stderr = stderr_bytes.decode(errors="replace").strip()
    stdout = stdout_bytes.decode(errors="replace").strip()
    return CompileCheckResult(
        ok=False,
        tool="cargo-check",
        message=(stderr or stdout or f"cargo check --tests exited {proc.returncode}")[:500],
    )


async def check_file(path: Path, language: str) -> CompileCheckResult:
    """Dispatch to the language-specific compile check."""
    lang = language.lower()
    if lang == "python":
        return await check_python(path)
    if lang == "typescript":
        return await check_typescript(path)
    if lang == "javascript":
        return await check_javascript(path)
    if lang == "rust":
        return await check_rust(path)
    return CompileCheckResult(
        ok=True, tool="skipped", message=f"no compile check for language: {language}"
    )
