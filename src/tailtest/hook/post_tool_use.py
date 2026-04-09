"""PostToolUse hook runtime (Phase 1 Task 1.5).

The async ``run`` function here is the real implementation. The
repo-root ``hooks/post_tool_use.py`` file is a thin shim that reads
stdin, calls ``run``, prints the result, and exits 0.

Responsibilities:

1. Parse the Claude Code PostToolUse payload: ``tool_name``,
   ``tool_input.file_path`` (and/or ``file_paths`` for MultiEdit).
2. Early exits (never run tests in these cases):
   a. Missing or unsupported tool
   b. No file_path in the payload
   c. The file is a test file itself
   d. The file is inside the tailtest source tree (self-edit exclusion,
      audit gap #15)
3. Lightweight manifest rescan (audit gap #2): if the edited file is a
   manifest (package.json, pyproject.toml, Cargo.toml, etc.), trigger
   a shallow ProjectScanner.scan_shallow() pass to refresh the project
   profile. Runs before the test run so the hot loop picks up new
   framework signals in the same turn.
4. Fresh config load (audit gap #4 + #7): ConfigLoader.load() runs per
   invocation, never cached.
5. Runner selection: ask the registry for any runner that can handle
   the project, dispatch by language based on the file suffix.
6. Native TIA: call ``runner.impacted([file_path])`` to get the
   affected test IDs.
7. Execute the impacted tests via ``runner.run(test_ids)``.
8. Baseline filtering: drop findings whose id is in the baseline so
   the user sees only new failures (per the BaselineManager contract).
9. Format the result as a compact JSON block suitable for Claude's
   next-turn context (5KB cap, audit gap #5).
10. Persist the full batch to ``.tailtest/reports/latest.json``.
11. Return the stdout payload for the shim to print, or ``None`` when
    nothing meaningful happened.

This runtime does NOT handle SIGINT itself. The repo-root shim is
responsible for installing signal handlers and for ``sys.exit(130)``
on interrupt, since that's a process-level concern. Tests cover the
logic in ``run``; SIGINT behavior is tested via a subprocess test.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

# Self-register runners at import time. This package is loaded via the
# repo-root hook shim, which inherits the tailtest venv PYTHONPATH, so
# the import chain here populates the default registry before we touch it.
import tailtest.core.runner.javascript  # noqa: F401, E402
import tailtest.core.runner.python  # noqa: F401, E402
from tailtest.core.baseline import BaselineManager
from tailtest.core.config import ConfigLoader
from tailtest.core.findings.schema import FindingBatch
from tailtest.core.runner import BaseRunner, RunnerNotAvailable, get_default_registry
from tailtest.core.scan import ProjectScanner

logger = logging.getLogger(__name__)

# Audit gap #5: same 5KB cap as the run_tests MCP tool.
_MAX_ADDITIONAL_CONTEXT_BYTES = 5 * 1024

_SUPPORTED_TOOLS = {"Edit", "Write", "MultiEdit"}

# Manifest files that should trigger a rescan of the project profile
# before running tests. When Claude adds a dependency to package.json,
# we want the next hook run to see it even though SessionStart has not
# fired again.
_MANIFEST_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "Gemfile",
    "composer.json",
    "go.mod",
    "requirements.txt",
    "Dockerfile",
    "tsconfig.json",
    "vitest.config.ts",
    "vitest.config.js",
    "jest.config.ts",
    "jest.config.js",
}

# Paths (relative fragments) that identify tailtest's own source. Files
# under these paths get a self-edit pass, the hook skips the hot loop
# entirely to avoid running tailtest against itself.
_SELF_EDIT_FRAGMENTS = (
    "/tailtest/src/tailtest/",
    "/tailtest/tests/",
)


@dataclass(frozen=True)
class HookResult:
    """What the hook runtime returns to the repo-root shim.

    ``stdout_json`` is either the JSON string the shim should print
    (the ``hookSpecificOutput`` envelope) or ``None`` when the hook
    decided to emit nothing. ``reason`` is a short diagnostic the
    shim can optionally log for debugging; it is never surfaced to
    Claude.
    """

    stdout_json: str | None
    reason: str


# --- Entry point -------------------------------------------------------


async def run(
    stdin_text: str,
    *,
    project_root: Path | None = None,
    now_utc: str | None = None,
) -> HookResult:
    """Process a PostToolUse payload and return the hook response.

    Parameters
    ----------
    stdin_text:
        Raw JSON received on the hook's stdin. Malformed input returns
        ``HookResult(None, "bad stdin")`` rather than raising, so the
        hot loop never crashes a Claude turn.
    project_root:
        Optional explicit project root. Defaults to the current working
        directory, which matches how the Claude Code hook is launched.
    now_utc:
        Injected timestamp for deterministic testing. If None the
        engine generates one.
    """
    del now_utc  # Phase 1 does not persist hook-level timestamps separately.

    root = (project_root or Path.cwd()).resolve()

    payload = _parse_stdin(stdin_text)
    if payload is None:
        return HookResult(None, "stdin is not valid JSON")

    tool_name = payload.get("tool_name") or ""
    if tool_name not in _SUPPORTED_TOOLS:
        return HookResult(None, f"unsupported tool: {tool_name}")

    changed_files = _extract_file_paths(payload)
    if not changed_files:
        return HookResult(None, "no file_path in payload")

    # Self-edit exclusion (audit gap #15): if any changed file is
    # inside tailtest's own source tree, skip the whole turn. We never
    # want the hook to run tailtest against itself during development.
    if any(_is_self_edit(f) for f in changed_files):
        return HookResult(None, "self-edit excluded")

    # Test-file short-circuit: if every changed file is itself a test,
    # there is nothing for tailtest to contribute in this turn. The
    # user is editing tests; running the tests against themselves would
    # at best reproduce what they already see.
    if all(_looks_like_test_file(f) for f in changed_files):
        return HookResult(None, "all changes are test files")

    # Load config fresh per invocation (audit gap #4/#7). Defaults are
    # fine if .tailtest/config.yaml is missing.
    config = ConfigLoader(root / ".tailtest").load()

    if config.depth.value == "off":
        return HookResult(None, "depth is off")

    # Lightweight manifest rescan (audit gap #2). Only runs on writes
    # that touched a known manifest file. The result goes to
    # .tailtest/profile.json via ProjectScanner.save_profile().
    manifest_rescanned = False
    if any(_is_manifest_file(f) for f in changed_files):
        try:
            scanner = ProjectScanner(root)
            profile = scanner.scan_shallow()
            scanner.save_profile(profile)
            manifest_rescanned = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("manifest rescan failed: %s", exc)

    runner = _pick_runner_for_file(root, changed_files[0])
    if runner is None:
        return HookResult(None, "no runner for this project")

    run_id = str(uuid4())

    # Native TIA: which tests does the runner think are impacted?
    try:
        test_ids = await runner.impacted(changed_files)
    except Exception as exc:  # noqa: BLE001
        logger.warning("impacted() failed: %s", exc)
        test_ids = []

    # Delta coverage preparation (Phase 1 Task 1.8a). Build a map of
    # {file_str: set[int]} from the tool payload so the runner can
    # intersect it with coverage data. Only PythonRunner supports
    # delta coverage in Phase 1; the JSRunner path is deferred. If
    # the payload is not an Edit/Write pair we can diff, the map is
    # empty and delta coverage is skipped automatically.
    added_lines = _build_added_lines(payload, changed_files)
    collect_coverage = bool(added_lines) and runner.language == "python"

    try:
        if collect_coverage:
            # Narrow to PythonRunner to expose the coverage-specific
            # parameters on runner.run(). The collect_coverage flag is
            # only set when runner.language == "python", so this cast
            # is safe and keeps the type checker happy without a
            # blanket ignore comment.
            from tailtest.core.runner.python import PythonRunner

            py_runner = runner if isinstance(runner, PythonRunner) else None
            if py_runner is not None:
                batch = await py_runner.run(
                    test_ids,
                    run_id=run_id,
                    timeout_seconds=30.0,
                    collect_coverage=True,
                    added_lines=added_lines,
                )
            else:
                batch = await runner.run(test_ids, run_id=run_id, timeout_seconds=30.0)
        else:
            batch = await runner.run(test_ids, run_id=run_id, timeout_seconds=30.0)
    except TimeoutError:
        timed_out = FindingBatch(
            run_id=run_id,
            depth=config.depth.value,
            summary_line="tailtest: timed out at 30s, skipping this turn",
            duration_ms=30_000.0,
        )
        _persist_report(root, timed_out)
        return HookResult(
            stdout_json=_format_additional_context(
                timed_out, manifest_rescanned=manifest_rescanned
            ),
            reason="runner timeout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner.run() failed: %s", exc)
        return HookResult(None, f"runner.run failed: {exc}")

    # Stamp the depth onto the batch so downstream consumers see it.
    batch = batch.model_copy(update={"depth": config.depth.value})

    # Apply baseline (drops findings whose id is in baseline).
    manager = BaselineManager(root / ".tailtest")
    batch = manager.apply_to(batch)

    _persist_report(root, batch)

    stdout_json = _format_additional_context(batch, manifest_rescanned=manifest_rescanned)
    return HookResult(stdout_json=stdout_json, reason="ok")


# --- Parsing helpers ---------------------------------------------------


def _parse_stdin(stdin_text: str) -> dict | None:
    """Parse the hook stdin payload, returning None on any failure.

    The hook must never raise on a malformed payload. A defensive
    return-None keeps the hot loop alive even when Claude Code sends
    something unexpected.
    """
    if not stdin_text or not stdin_text.strip():
        return None
    try:
        data = json.loads(stdin_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _extract_file_paths(payload: dict) -> list[Path]:
    """Return the list of absolute Paths mentioned in the payload.

    Handles Edit (tool_input.file_path), Write (same shape), and
    MultiEdit (tool_input.file_path + tool_input.edits[*]).
    """
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return []
    primary = tool_input.get("file_path")
    paths: list[Path] = []
    if isinstance(primary, str) and primary:
        paths.append(Path(primary))
    # MultiEdit has an edits array, but each edit operates on the same
    # file_path in the Claude Code contract, so no extra files to add.
    # If a future Claude Code release ships a multi-file variant we can
    # extend this block without touching callers.
    return paths


def _looks_like_test_file(file_path: Path) -> bool:
    """Return True if a file path looks like a test file by convention."""
    name = file_path.name.lower()
    if name.startswith("test_") and name.endswith(".py"):
        return True
    return name.endswith(
        (
            ".test.ts",
            ".test.tsx",
            ".test.js",
            ".test.jsx",
            ".spec.ts",
            ".spec.tsx",
            ".spec.js",
            ".spec.jsx",
        )
    )


def _is_self_edit(file_path: Path) -> bool:
    """Return True if the edited file is inside tailtest's own source.

    Uses path-fragment matching so the check works whether the path
    comes in as /Users/... (macOS) or /home/... (Linux) or a relative
    form. The fragments intentionally include both the v1 and v2 repo
    locations so stale v1 paths still get skipped.
    """
    norm = str(file_path).replace("\\", "/")
    return any(fragment in norm for fragment in _SELF_EDIT_FRAGMENTS)


def _is_manifest_file(file_path: Path) -> bool:
    return file_path.name in _MANIFEST_FILENAMES


def _build_added_lines(
    payload: dict,
    changed_files: list[Path],
) -> dict[str, set[int]]:
    """Extract added line numbers from an Edit/Write tool payload.

    Returns a map keyed by file path string (matching the format
    coverage.py uses for its own file keys) to a set of 1-indexed
    added line numbers.

    For a ``Write`` tool: the entire file content is treated as
    added. For an ``Edit`` tool: uses difflib on old_string vs
    new_string, with line numbers relative to the file's full
    content after applying the edit (best-effort, uses the on-disk
    file if readable). MultiEdit is treated the same as Edit for
    now because the Claude Code contract does not expose a full
    diff envelope.
    """
    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict) or not changed_files:
        return {}

    result: dict[str, set[int]] = {}
    primary = changed_files[0]

    if tool_name == "Write":
        content = tool_input.get("content")
        if isinstance(content, str):
            from tailtest.core.coverage import write_added_lines

            lines = write_added_lines(content)
            if lines:
                result[str(primary)] = lines
        return result

    if tool_name in ("Edit", "MultiEdit"):
        # For Edit we need the full file to compute real line numbers.
        # Read the current on-disk version (which already reflects the
        # applied edit, since the hook fires AFTER the tool ran).
        try:
            new_content = primary.read_text(encoding="utf-8")
        except OSError:
            return {}

        new_string = tool_input.get("new_string") or ""

        if not new_string and "edits" in tool_input:
            # MultiEdit: concatenate all new_string blocks so the
            # diff sees the union of changes. We only track the
            # new_string side because we compare against the current
            # on-disk file, which already reflects the applied edits.
            edits = tool_input.get("edits") or []
            new_string = "\n".join(e.get("new_string", "") for e in edits if isinstance(e, dict))

        if not new_string:
            return {}

        # Find where new_string lives in the current file and return
        # those line numbers as "added". This is a best-effort
        # approximation; if new_string appears multiple times or does
        # not appear literally (because Claude added extra context),
        # we fall back to "the whole file is added".
        file_lines = new_content.splitlines()
        new_lines = new_string.splitlines()
        if not new_lines:
            return {}

        match_start = _find_block_start(file_lines, new_lines)
        if match_start is None:
            # Could not locate the block; fall back to treating the
            # whole file as new. Safer to over-report new lines than
            # to skip delta coverage entirely.
            from tailtest.core.coverage import write_added_lines

            result[str(primary)] = write_added_lines(new_content)
            return result

        added_line_numbers = {
            match_start + i + 1  # 1-indexed
            for i in range(len(new_lines))
        }
        result[str(primary)] = added_line_numbers
        return result

    return {}


def _find_block_start(haystack: Sequence[str], needle: Sequence[str]) -> int | None:
    """Return the 0-indexed start of ``needle`` inside ``haystack``, or None.

    Simple linear search. Performance is fine because both sequences
    come from a single file, typically under a few hundred lines.
    ``Sequence`` covariance avoids list invariance friction with
    ``list[LiteralString]`` callers.
    """
    if not needle or not haystack or len(needle) > len(haystack):
        return None
    needle_list = list(needle)
    haystack_list = list(haystack)
    for i in range(len(haystack_list) - len(needle_list) + 1):
        if haystack_list[i : i + len(needle_list)] == needle_list:
            return i
    return None


# --- Runner selection --------------------------------------------------


def _pick_runner_for_file(root: Path, file_path: Path) -> BaseRunner | None:
    """Pick the best runner for the given file's language.

    Tries the full registry via ``all_for_project`` first so every
    registered runner gets a chance to claim the project. Then filters
    by language based on the file suffix so a Python edit routes to
    the Python runner even in a monorepo with both Python and JS.
    """
    registry = get_default_registry()
    try:
        candidates = registry.all_for_project(root)
    except RunnerNotAvailable:
        return None
    if not candidates:
        return None

    suffix = file_path.suffix.lower()
    lang_by_suffix = {
        ".py": "python",
        ".ts": "javascript",
        ".tsx": "javascript",
        ".mts": "javascript",
        ".cts": "javascript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
    }
    wanted = lang_by_suffix.get(suffix)
    if wanted is None:
        # Unknown language, fall back to whichever runner was first
        # registered so manifest edits still get a test run.
        return candidates[0]

    for runner in candidates:
        if runner.language == wanted:
            return runner
    return None


# --- Output formatting -------------------------------------------------


def _format_additional_context(
    batch: FindingBatch,
    *,
    manifest_rescanned: bool,
) -> str:
    """Build the hookSpecificOutput JSON payload for Claude's next turn.

    Format:
    - ``summary_line`` always present (the one-liner the terminal
      reporter uses).
    - Up to 5 findings in a compact form, ordered by severity then
      first-seen order.
    - If more than 5 findings exist, the response truncates and adds
      a footer line pointing at ``.tailtest/reports/latest.json``.
    - The full block is capped at 5 KB (audit gap #5).
    """
    top_findings = sorted(
        batch.findings,
        key=lambda f: _severity_rank(f.severity.value),
    )[:5]

    lines: list[str] = [batch.summary_line]
    if manifest_rescanned:
        lines.append("(manifest rescan: profile refreshed before this run)")

    # Delta coverage line (Phase 1 Task 1.8a). Only shows when the
    # runner computed delta coverage this run. Format: one line with
    # the percentage + the count of uncovered new lines, plus up to
    # 3 uncovered file:line entries so Claude can target them.
    if batch.delta_coverage_pct is not None:
        uncovered_count = len(batch.uncovered_new_lines)
        if uncovered_count == 0:
            lines.append(f"delta coverage: {batch.delta_coverage_pct:.1f}%")
        else:
            lines.append(
                f"delta coverage: {batch.delta_coverage_pct:.1f}% "
                f"({uncovered_count} new line{'s' if uncovered_count != 1 else ''} uncovered)"
            )
            for entry in batch.uncovered_new_lines[:3]:
                file_str = entry.get("file", "?")
                line_num = entry.get("line", 0)
                lines.append(f"  uncovered: {file_str}:{line_num}")
            if uncovered_count > 3:
                lines.append(f"  ... {uncovered_count - 3} more uncovered new lines")

    for f in top_findings:
        location = f"{f.file}:{f.line}" if f.line else str(f.file)
        snippet = f"  [{f.severity.value}] {location} {f.message}"
        lines.append(snippet)
        if f.claude_hint:
            lines.append(f"    hint: {f.claude_hint}")

    extra = len(batch.findings) - len(top_findings)
    if extra > 0:
        lines.append(f"... {extra} more findings, see .tailtest/reports/latest.json")

    additional_context = "\n".join(lines)
    additional_context = _truncate(additional_context)

    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    }
    return json.dumps(envelope)


def _severity_rank(severity: str) -> int:
    ranks = {
        "critical": 0,
        "high": 1,
        "medium": 2,
        "low": 3,
        "info": 4,
    }
    return ranks.get(severity, 5)


def _truncate(text: str) -> str:
    """Cap the additional_context at 5KB with a clear footer."""
    if len(text.encode("utf-8")) <= _MAX_ADDITIONAL_CONTEXT_BYTES:
        return text
    limit = _MAX_ADDITIONAL_CONTEXT_BYTES - 120
    clipped = text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return (
        clipped + "\n... (truncated at 5KB, see .tailtest/reports/latest.json for the full batch)"
    )


# --- Persistence -------------------------------------------------------


def _persist_report(root: Path, batch: FindingBatch) -> None:
    """Write the full batch to .tailtest/reports/latest.json (best effort)."""
    try:
        reports_dir = root / ".tailtest" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "latest.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist report: %s", exc)


# --- Symbol re-exports for tests ---------------------------------------

__all__ = [
    "HookResult",
    "run",
    # Private helpers are re-exported for the unit tests so they can
    # exercise isolated behavior without going through the full run()
    # pipeline. Tests importing private names is a deliberate
    # simplification for Phase 1; the test file prefixes them with
    # an underscore-aware pattern so ruff does not fuss.
    "_parse_stdin",
    "_extract_file_paths",
    "_looks_like_test_file",
    "_is_self_edit",
    "_is_manifest_file",
    "_format_additional_context",
    "_truncate",
    "_MANIFEST_FILENAMES",
    "_SELF_EDIT_FRAGMENTS",
]
