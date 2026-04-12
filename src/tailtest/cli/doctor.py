# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""``tailtest doctor`` CLI command (Phase 1 Task 1.12).

Health check for the user's environment + tailtest installation.
Per audit gap #17, the full checklist:

- Python version ≥ 3.11
- claude CLI on PATH
- MCP server responds to initialize
- Plugin manifest valid (if installed as a plugin)
- At least one runner detected for the current project
- Runner binaries actually installed
- .tailtest/ writable
- config.yaml + baseline.yaml parseable
- events.jsonl writable
- LLM resolver finds a backend (warn if not, since LLM features
  degrade gracefully)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import click


class CheckResult(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass
class Check:
    name: str
    result: CheckResult
    message: str
    detail: str = ""

    def render(self, *, color: bool) -> str:
        if not color:
            symbol = {
                CheckResult.PASS: "[PASS]",
                CheckResult.WARN: "[WARN]",
                CheckResult.FAIL: "[FAIL]",
            }[self.result]
            return f"{symbol} {self.name}: {self.message}"
        symbol_colored = {
            CheckResult.PASS: click.style("[PASS]", fg="green"),
            CheckResult.WARN: click.style("[WARN]", fg="yellow"),
            CheckResult.FAIL: click.style("[FAIL]", fg="red"),
        }[self.result]
        return f"{symbol_colored} {self.name}: {self.message}"


@click.command("doctor")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    required=False,
    default=None,
)
@click.option("--verbose", is_flag=True, default=False, help="Show details for every check.")
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help="Project root. Overridden by the positional PATH argument when both are given.",
)
def doctor_cmd(path: str | None, verbose: bool, project_root: str | None) -> None:
    """Check that tailtest's environment is healthy.

    Runs ~10 checks and prints PASS/WARN/FAIL per check. Exit code:
    0 if all PASS or WARN; 1 if any FAIL.

    PATH is an optional positional shorthand for ``--project-root``.

    Examples:

      tailtest doctor                  # check cwd
      tailtest doctor .                # same, positional form
      tailtest doctor /path/to/repo
    """
    chosen = path or project_root
    root = Path(chosen) if chosen else Path.cwd()
    root = root.resolve()

    checks: list[Check] = []

    checks.append(_check_python_version())
    checks.append(_check_claude_cli())
    checks.append(_check_mcp_server_handshake())
    python_runner_detected = _check_runner_detection(root, checks)
    if python_runner_detected:
        checks.append(_check_pytest_binary(root))
    checks.append(_check_tailtest_dir_writable(root))
    checks.append(_check_config_valid(root))
    checks.append(_check_baseline_valid(root))
    checks.append(_check_events_writable(root))
    checks.append(_check_llm_resolver())
    checks.append(_check_plugin_manifest())

    color = sys.stdout.isatty()
    for c in checks:
        click.echo(c.render(color=color))
        if verbose and c.detail:
            for line in c.detail.splitlines():
                click.echo(f"        {line}")

    fail_count = sum(1 for c in checks if c.result == CheckResult.FAIL)
    warn_count = sum(1 for c in checks if c.result == CheckResult.WARN)
    pass_count = sum(1 for c in checks if c.result == CheckResult.PASS)

    click.echo()
    click.echo(f"tailtest doctor: {pass_count} pass · {warn_count} warn · {fail_count} fail")

    sys.exit(1 if fail_count > 0 else 0)


# --- Individual checks ---------------------------------------------------


def _check_python_version() -> Check:
    # The < (3, 11) branch is technically unreachable because pyproject.toml's
    # `requires-python = ">=3.11"` makes pip refuse to install tailtest on older
    # Pythons. We keep the defensive check anyway for the rare case of a manual
    # install (e.g. cloning + running directly without pip), where it acts as a
    # clearer error than an obscure import failure later. ruff UP036 noqa is
    # intentional.
    if sys.version_info < (3, 11):  # noqa: UP036
        return Check(
            name="Python version",
            result=CheckResult.FAIL,
            message=f"need Python 3.11+, got {sys.version_info.major}.{sys.version_info.minor}",
        )
    return Check(
        name="Python version",
        result=CheckResult.PASS,
        message=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )


def _check_claude_cli() -> Check:
    path = shutil.which("claude")
    if path is None:
        return Check(
            name="Claude CLI on PATH",
            result=CheckResult.WARN,
            message="not found — LLM features (test gen, deep scan, judge) will be unavailable",
        )
    try:
        result = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return Check(
                name="Claude CLI on PATH",
                result=CheckResult.PASS,
                message=path,
                detail=result.stdout.strip(),
            )
        return Check(
            name="Claude CLI on PATH",
            result=CheckResult.WARN,
            message=f"found at {path} but `claude --version` failed (exit {result.returncode})",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check(
            name="Claude CLI on PATH",
            result=CheckResult.WARN,
            message=f"found at {path} but invocation failed: {exc}",
        )


def _check_mcp_server_handshake() -> Check:
    """Spawn `tailtest mcp-serve` in a subprocess, send initialize, expect a response."""
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "tailtest", "mcp-serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        return Check(
            name="MCP server handshake",
            result=CheckResult.FAIL,
            message=f"could not start tailtest mcp-serve: {exc}",
        )

    init_msg = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2024-11-05","capabilities":{}}}'
    )
    try:
        stdout, stderr = process.communicate(input=init_msg + "\n", timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return Check(
            name="MCP server handshake",
            result=CheckResult.FAIL,
            message="server didn't respond within 10s",
        )

    first_line = stdout.strip().split("\n", 1)[0] if stdout else ""
    if not first_line:
        return Check(
            name="MCP server handshake",
            result=CheckResult.FAIL,
            message="no response on stdout",
            detail=stderr.strip()[:500],
        )

    try:
        data = json.loads(first_line)
    except json.JSONDecodeError as exc:
        return Check(
            name="MCP server handshake",
            result=CheckResult.FAIL,
            message=f"non-JSON response: {exc}",
        )

    if data.get("result", {}).get("serverInfo", {}).get("name") == "tailtest":
        version = data["result"]["serverInfo"].get("version", "?")
        return Check(
            name="MCP server handshake",
            result=CheckResult.PASS,
            message=f"responding (server v{version})",
        )
    return Check(
        name="MCP server handshake",
        result=CheckResult.FAIL,
        message="unexpected response shape",
        detail=first_line[:500],
    )


def _check_runner_detection(root: Path, checks: list[Check]) -> bool:
    """Append a runner-detection Check to *checks*. Returns True if a Python runner was found."""
    from tailtest.core.runner import _register_all_runners, get_default_registry

    _register_all_runners()
    registry = get_default_registry()
    runners = registry.all_for_project(root)
    if not runners:
        unavailable = registry.unavailable_reasons(root)
        if unavailable:
            detail = "; ".join(f"{n}: {r}" for n, r in unavailable.items())
            checks.append(Check(
                name="Runner detection",
                result=CheckResult.WARN,
                message="runner configured but binary missing",
                detail=detail,
            ))
        else:
            checks.append(Check(
                name="Runner detection",
                result=CheckResult.WARN,
                message="no runners detected for the current project",
                detail=f"project root: {root}",
            ))
        return False
    names = ", ".join(r.name for r in runners)
    checks.append(Check(name="Runner detection", result=CheckResult.PASS, message=names))
    return any(r.language == "python" for r in runners)


def _check_pytest_binary(root: Path) -> Check:
    """Resolve pytest the same way PythonRunner does — venv first, then PATH.

    Reports WHERE pytest is coming from so the user can spot venv mismatches:
    if the path points at tailtest's own venv (not the target's), tests will
    collection-fail on missing target deps. Caught by Checkpoint E dogfood.
    """
    from tailtest.core.runner.python import PythonRunner

    runner = PythonRunner(root)
    resolved = runner._resolve_pytest_path()  # noqa: SLF001
    if resolved is None:
        return Check(
            name="pytest binary",
            result=CheckResult.WARN,
            message="pytest not found in project venv or on PATH (Python runner unavailable)",
        )
    # Detect "venv mismatch": resolved path is outside the target project root.
    inside_target = False
    try:
        Path(resolved).resolve().relative_to(root)
        inside_target = True
    except ValueError:
        pass
    if inside_target:
        return Check(name="pytest binary", result=CheckResult.PASS, message=resolved)
    # Resolved from PATH — works for self-test, but flag the risk for dogfood.
    return Check(
        name="pytest binary",
        result=CheckResult.PASS,
        message=f"{resolved} (PATH; no project venv detected)",
    )


def _check_tailtest_dir_writable(root: Path) -> Check:
    tailtest_dir = root / ".tailtest"
    try:
        tailtest_dir.mkdir(parents=True, exist_ok=True)
        probe = tailtest_dir / ".doctor-probe"
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return Check(
            name=".tailtest/ directory",
            result=CheckResult.FAIL,
            message=f"not writable: {exc}",
        )
    return Check(name=".tailtest/ directory", result=CheckResult.PASS, message=str(tailtest_dir))


def _check_config_valid(root: Path) -> Check:
    from tailtest.core.config import ConfigLoader

    loader = ConfigLoader(root / ".tailtest")
    if not loader.exists():
        return Check(
            name="config.yaml",
            result=CheckResult.PASS,
            message="not present (will use defaults)",
        )
    try:
        config = loader.load()
        return Check(
            name="config.yaml",
            result=CheckResult.PASS,
            message=f"loaded · depth={config.depth.value}",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="config.yaml",
            result=CheckResult.FAIL,
            message=f"failed to parse: {exc}",
        )


def _check_baseline_valid(root: Path) -> Check:
    from tailtest.core.baseline import BaselineManager

    manager = BaselineManager(root / ".tailtest")
    if not manager.exists():
        return Check(
            name="baseline.yaml",
            result=CheckResult.PASS,
            message="not present (will be lazy-generated on first green run)",
        )
    try:
        baseline = manager.load()
        return Check(
            name="baseline.yaml",
            result=CheckResult.PASS,
            message=f"loaded · {len(baseline.entries)} entries",
        )
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="baseline.yaml",
            result=CheckResult.FAIL,
            message=f"failed to parse: {exc}",
        )


def _check_events_writable(root: Path) -> Check:
    from tailtest.core.events import EventWriter

    tailtest_dir = root / ".tailtest"
    try:
        writer = EventWriter(tailtest_dir)
        # Don't actually write — just verify the directory is writable. The
        # _check_tailtest_dir_writable check above already validates real writes.
        _ = writer.events_path
        return Check(name="events.jsonl writer", result=CheckResult.PASS, message="ready")
    except Exception as exc:  # noqa: BLE001
        return Check(
            name="events.jsonl writer",
            result=CheckResult.WARN,
            message=f"writer setup failed: {exc}",
        )


def _check_llm_resolver() -> Check:
    """Audit gap #9 minor: graceful WARN when no LLM backend is available."""
    from tailtest.llm.resolver import is_claude_code_available, resolve_judge_model

    backends: list[str] = []
    if is_claude_code_available():
        backends.append("claude CLI")
    model = resolve_judge_model()
    if model:
        backends.append(model)

    if not backends:
        return Check(
            name="LLM resolver",
            result=CheckResult.WARN,
            message=(
                "no LLM backend found — tailtest will work for native runners but "
                "test generation, deep scan, and LLM-judge will be unavailable until "
                "a backend is configured (claude CLI on PATH, ANTHROPIC_API_KEY, etc.)"
            ),
        )
    return Check(name="LLM resolver", result=CheckResult.PASS, message=" + ".join(backends))


def _check_plugin_manifest() -> Check:
    """If we appear to be inside a tailtest plugin install, validate plugin.json."""
    # Look for the manifest at the standard location relative to the source tree.
    manifest_path = Path(__file__).resolve().parents[3] / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        return Check(
            name="Plugin manifest",
            result=CheckResult.PASS,
            message="not running as a plugin (n/a)",
        )
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return Check(
            name="Plugin manifest",
            result=CheckResult.FAIL,
            message=f"not valid JSON: {exc}",
        )
    if "name" not in data or "version" not in data:
        return Check(
            name="Plugin manifest",
            result=CheckResult.FAIL,
            message="missing required fields",
        )
    return Check(
        name="Plugin manifest",
        result=CheckResult.PASS,
        message=f"valid · {data['name']} v{data['version']}",
    )
