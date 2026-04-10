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
10. Persist the full batch to ``.tailtest/reports/latest.json`` and
    mirror an HTML view to ``.tailtest/reports/latest.html`` +
    ``<iso>.html`` (Phase 2 Task 2.6).
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
import time
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
from tailtest.core.config import Config, ConfigLoader, DepthMode
from tailtest.core.findings.schema import Finding, FindingBatch
from tailtest.core.runner import BaseRunner, RunnerNotAvailable, get_default_registry
from tailtest.core.scan import ProjectScanner
from tailtest.core.scan.profile import AISurface
from tailtest.security.sast.semgrep import SemgrepRunner
from tailtest.security.sca.manifests import (
    PackageRef,
    diff_manifests,
    parse_package_json,
    parse_pyproject_toml,
)
from tailtest.security.sca.osv import OSVLookup
from tailtest.security.secrets.gitleaks import GitleaksRunner

logger = logging.getLogger(__name__)

# Audit gap #5: same 5KB cap as the run_tests MCP tool.
_MAX_ADDITIONAL_CONTEXT_BYTES = 5 * 1024

# Tracks which files have already received the vibe-coder gen offer this
# session. Reset per process (per session), which is correct.
_gen_offered: set[str] = set()

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

# Manifest files that feed the OSV SCA lookup. Phase 2 ships parsers
# for the two default modern-Python and modern-JS paths; the larger
# _MANIFEST_FILENAMES set is used for profile-rescan purposes above.
_SCA_MANIFEST_PARSERS = {
    "pyproject.toml": parse_pyproject_toml,
    "package.json": parse_package_json,
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
    hook_start_monotonic = time.monotonic()

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
                timed_out,
                manifest_rescanned=manifest_rescanned,
                auto_offer_suggestions=None,
            ),
            reason="runner timeout",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("runner.run() failed: %s", exc)
        return HookResult(None, f"runner.run failed: {exc}")

    # Stamp the depth onto the batch so downstream consumers see it.
    batch = batch.model_copy(update={"depth": config.depth.value})

    # Security phase (Phase 2 Task 2.5). Runs after the test phase so
    # a failing test never gets buried under security noise: if any
    # test failed, we skip the scanner trio entirely and surface test
    # results first. Otherwise we call gitleaks / semgrep / OSV per
    # the depth gating rules and merge results into the same batch
    # BEFORE baseline filtering, so the baseline applies uniformly to
    # test + security findings.
    security_findings: list[Finding] = []
    if batch.tests_failed == 0:
        security_findings = await _run_security_phase(
            root=root,
            changed_files=changed_files,
            config=config,
            depth=config.depth,
            run_id=run_id,
        )
    else:
        logger.info(
            "skipping security phase: %d test(s) failed",
            batch.tests_failed,
        )

    if security_findings:
        merged_findings = list(batch.findings) + security_findings
        batch = batch.model_copy(update={"findings": merged_findings})

    # Apply baseline (drops findings whose id is in baseline).
    manager = BaselineManager(root / ".tailtest")
    batch = manager.apply_to(batch)

    # Recompute the summary line so it reflects post-baseline state
    # and carries the new-security-issue count plus the total hook
    # duration. We do this AFTER baseline so the "new" count excludes
    # findings the user has already acknowledged.
    hook_duration_s = time.monotonic() - hook_start_monotonic
    batch = batch.model_copy(
        update={
            "summary_line": _build_summary_line(batch, hook_duration_s),
        }
    )

    _persist_report(root, batch)

    # Auto-offer test generation (Phase 1 Task 1.5a). Runs after the
    # test batch is finalized but before the context is formatted so
    # the offer can append to the same envelope. Gated by the
    # `notifications.auto_offer_generation` config flag (default on).
    # Debounced via `.tailtest/session-state.json` so the same symbol
    # is not re-offered twice in a session.
    auto_offer_suggestions: list[str] = []
    if config.notifications.auto_offer_generation:
        auto_offer_suggestions = _collect_auto_offer_suggestions(
            root=root,
            changed_files=changed_files,
            payload=payload,
        )

    # Vibe-coder proactive gen offer (Phase 3 Task 3.7). Fires when the
    # project is vibe-coded, the changed file is a .py file with a
    # function definition, and no tests ran for that file this invocation.
    # At most once per file per session (tracked by _gen_offered set).
    vibe_gen_offer: str | None = _maybe_vibe_gen_offer(
        root=root,
        changed_files=changed_files,
        payload=payload,
        tests_ran=batch.tests_passed + batch.tests_failed,
    )

    # Recommendation surface (Phase 3 Task 3.4). Fire at most once per
    # session via a flag file. SOFT append -- never replaces test output.
    rec_surface_line = _maybe_surface_rec_line(root)

    # AI checks depth-mode branch (Phase 3 Task 3.5 / Task 3.6). When the
    # project is an AI agent, ai_checks_enabled is True, and the depth is
    # thorough or above, run the LLM-judge assertions and append any
    # findings to the context.
    ai_checks_note = _maybe_build_ai_checks_note(root, config)
    llm_judge_lines: list[str] = []
    if ai_checks_note is not None:
        try:
            tool_input_dict = payload.get("tool_input") or {}
            if not isinstance(tool_input_dict, dict):
                tool_input_dict = {}
            tool_output_raw = payload.get("tool_response", payload.get("tool_output", ""))
            if not isinstance(tool_output_raw, str):
                tool_output_raw = str(tool_output_raw)
            llm_judge_lines = await _run_llm_judge(
                project_root=root,
                tool_name=tool_name,
                tool_input=tool_input_dict,
                tool_output=tool_output_raw[:2000],
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM-judge invocation failed: %s", exc)

    stdout_json = _format_additional_context(
        batch,
        manifest_rescanned=manifest_rescanned,
        auto_offer_suggestions=auto_offer_suggestions,
        vibe_gen_offer=vibe_gen_offer,
        rec_surface_line=rec_surface_line,
        ai_checks_note=ai_checks_note,
        llm_judge_lines=llm_judge_lines,
    )
    return HookResult(stdout_json=stdout_json, reason="ok")


def _maybe_build_ai_checks_note(root: Path, config: Config) -> str | None:
    """Return an AI checks active note, or None.

    Fires when ALL of these are true:
    - profile.ai_surface == AISurface.AGENT
    - profile.ai_checks_enabled is True (user accepted)
    - config.depth is thorough or paranoid

    Returns None silently for non-agent projects, dismissed users, or
    standard/quick/off depth. All failures are caught and logged.
    """
    try:
        if config.depth not in (DepthMode.THOROUGH, DepthMode.PARANOID):
            return None

        scanner = ProjectScanner(root)
        profile = scanner.load_profile()
        if profile is None:
            return None

        if profile.ai_surface != AISurface.AGENT:
            return None

        if profile.ai_checks_enabled is not True:
            return None

        return "tailtest: AI checks active (thorough depth). LLM-judge assertions firing."
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI checks note failed: %s", exc)
        return None


async def _run_llm_judge(
    project_root: Path,
    tool_name: str,
    tool_input: dict,
    tool_output: str,
    run_id: str,
) -> list[str]:
    """Run LLM-judge assertions. Returns list of finding lines for additionalContext."""
    from tailtest.core.assertions.llm_judge import LLMJudge

    judge = LLMJudge(project_root)
    lines: list[str] = []

    # Check tool-call correctness
    ctx = f"Tool {tool_name} was called on this project."
    result = await judge.check_tool_call_correctness(tool_name, tool_input, ctx, run_id=run_id)
    if result.verdict == "fail":
        lines.append(f"[llm-judge] tool_call_correctness: FAIL -- {result.reasoning}")

    # Check PII in output
    if tool_output:
        pii = await judge.check_pii_leakage(tool_output, run_id=run_id)
        if pii.verdict == "fail":
            lines.append(f"[llm-judge] pii_leakage: FAIL -- {pii.reasoning}")

    return lines


def _maybe_surface_rec_line(root: Path) -> str | None:
    """Return a one-liner recommendation surface line, or None.

    Fires at most once per session by checking for a flag file at
    ``.tailtest/rec_surfaced.flag``. If the flag exists, returns None
    (already surfaced this session). If it does not exist, runs the
    engine, checks for high-priority active recommendations, and either
    creates the flag + returns the line, or returns None when there are
    no high-priority recs to surface.

    All failures are caught and logged; this path must never raise.
    """
    try:
        tailtest_dir = root / ".tailtest"
        flag_path = tailtest_dir / "rec_surfaced.flag"
        if flag_path.exists():
            return None

        from tailtest.core.recommendations.store import DismissalStore
        from tailtest.core.recommender.engine import RecommendationEngine
        from tailtest.core.scan import ProjectScanner

        scanner = ProjectScanner(root)
        profile = scanner.load_profile()
        if profile is None:
            return None

        engine = RecommendationEngine()
        recs = engine.compute(profile)
        store = DismissalStore(root)
        recs = store.apply(recs)
        high_active = [r for r in recs if r.priority == "high" and not r.is_dismissed]
        if not high_active:
            return None

        # Create the flag so subsequent PostToolUse calls in this session skip.
        try:
            tailtest_dir.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not write rec_surfaced.flag: %s", exc)

        count = len(high_active)
        noun = "recommendation" if count == 1 else "recommendations"
        return f"tailtest: {count} new {noun} -- run /tailtest for details."
    except Exception as exc:  # noqa: BLE001
        logger.warning("rec surface check failed: %s", exc)
        return None


def _maybe_vibe_gen_offer(
    *,
    root: Path,
    changed_files: list[Path],
    payload: dict,
    tests_ran: int,
) -> str | None:
    """Return a proactive gen offer string, or None.

    Fires when ALL of these are true:
    - profile.likely_vibe_coded is True
    - The changed file is a .py file that contains a function definition
      (i.e. "def " appears in the file's current content)
    - No tests ran for this invocation (tests_ran == 0)
    - The file has not already received this offer in the current session
      (tracked by the module-level _gen_offered set)

    All failures are swallowed and logged -- this path must never raise.
    """
    try:
        # Only fire for a single primary .py file.
        if not changed_files:
            return None
        primary = changed_files[0]
        if primary.suffix.lower() != ".py":
            return None

        # Check vibe-coded flag from the saved profile.
        scanner = ProjectScanner(root)
        profile = scanner.load_profile()
        if profile is None:
            return None
        if not getattr(profile, "likely_vibe_coded", False):
            return None

        # Only fire when no tests ran this invocation.
        if tests_ran > 0:
            return None

        # Check that the file contains a function definition.
        try:
            content = primary.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if "def " not in content:
            return None

        # Check the per-session deduplication set.
        file_key = str(primary.resolve())
        if file_key in _gen_offered:
            return None
        _gen_offered.add(file_key)

        # Build a display path relative to root for readability.
        try:
            display_path = str(primary.resolve().relative_to(root))
        except ValueError:
            display_path = str(primary)

        return (
            f"tailtest: no tests found for this function. "
            f"Run /tailtest:gen {display_path} to generate a starter test."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("vibe gen offer check failed: %s", exc)
        return None


def _collect_auto_offer_suggestions(
    *,
    root: Path,
    changed_files: list[Path],
    payload: dict,
) -> list[str]:
    """Return pre-formatted auto-offer suggestion lines for the next turn.

    For each changed Python file, identify pure functions that have
    no matching test, and return "consider running /tailtest:gen X"
    lines. Honors the session-state debounce so each `(file, symbol)`
    pair is offered at most once per session. Idempotent writes to
    session-state.json happen here.
    """
    from tailtest.core.generator.heuristics import find_uncovered_functions
    from tailtest.core.session_state import load_session_state, save_session_state

    # Extract the session id from the payload; fall back to None so
    # the session state loader treats it as "I don't know, preserve
    # what's on disk".
    session_id = None
    raw_session_id = payload.get("session_id")
    if isinstance(raw_session_id, str) and raw_session_id:
        session_id = raw_session_id

    tailtest_dir = root / ".tailtest"
    state = load_session_state(tailtest_dir, current_session_id=session_id)

    suggestions: list[str] = []
    changed_something = False

    for changed_file in changed_files:
        # Only Python files get the heuristic in Phase 1. TS/JS
        # would need a different parser.
        if changed_file.suffix.lower() != ".py":
            continue
        try:
            absolute = changed_file.resolve()
        except OSError:
            continue
        candidates = find_uncovered_functions(absolute, root)
        for candidate in candidates:
            file_key = str(absolute)
            if state.has_seen(file_key, candidate.name):
                continue
            # Build the human-readable suggestion. Kept short so the
            # 5 KB truncation budget stays reasonable.
            try:
                rel = absolute.relative_to(root)
                display_path = str(rel)
            except ValueError:
                display_path = str(absolute)
            suggestions.append(
                f"tailtest: `{candidate.name}` in {display_path}:{candidate.lineno} "
                f"has no test. Run `/tailtest:gen {display_path}` to generate one."
            )
            state.mark_seen(file_key, candidate.name)
            changed_something = True
            # Cap at 3 suggestions per hook run so a huge refactor
            # does not flood the context. Additional candidates wait
            # for subsequent edits.
            if len(suggestions) >= 3:
                break
        if len(suggestions) >= 3:
            break

    if changed_something:
        save_session_state(tailtest_dir, state)

    return suggestions


# --- Parsing helpers ---------------------------------------------------


def _parse_stdin(stdin_text: str) -> dict | None:
    """Parse the hook stdin payload, returning None on any failure.

    The hook must never raise on a malformed payload. A defensive
    return-None keeps the hot loop alive even when Claude Code sends
    something unexpected. Phase 2 Task 2.10 follow-up: each failure
    mode now logs an INFO-level diagnostic so a misbehaving hook is
    distinguishable from a "hook not installed" no-op when the user
    runs Claude with ``--debug`` or tails the hook log. Empty stdin
    is intentionally silent because Claude Code regularly invokes
    hooks with no payload.
    """
    if not stdin_text or not stdin_text.strip():
        return None
    try:
        data = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        logger.info("post_tool_use: stdin is not valid JSON (%s), skipping turn", exc)
        return None
    if not isinstance(data, dict):
        logger.info(
            "post_tool_use: stdin is JSON but not an object (got %s), skipping turn",
            type(data).__name__,
        )
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
    auto_offer_suggestions: list[str] | None = None,
    vibe_gen_offer: str | None = None,
    rec_surface_line: str | None = None,
    ai_checks_note: str | None = None,
    llm_judge_lines: list[str] | None = None,
) -> str:
    """Build the hookSpecificOutput JSON payload for Claude's next turn.

    Format:
    - ``summary_line`` always present (the one-liner the terminal
      reporter uses).
    - Up to 5 findings in a compact form, ordered by severity then
      first-seen order.
    - If more than 5 findings exist, the response truncates and adds
      a footer line pointing at ``.tailtest/reports/latest.json``.
    - Optional auto-offer test generation suggestions appended as a
      separate block at the end so the user (and Claude) see the
      test findings first. Maximum 3 suggestions per run.
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

    # Auto-offer suggestions go after the findings so test failures
    # remain the most urgent part of the context. Each suggestion is
    # already a single compact line from _collect_auto_offer_suggestions.
    if auto_offer_suggestions:
        lines.append("")  # blank separator
        for suggestion in auto_offer_suggestions:
            lines.append(suggestion)

    # Vibe-coder gen offer (Phase 3 Task 3.7): proactive offer to generate
    # a starter test when the project is vibe-coded and no tests ran.
    if vibe_gen_offer:
        lines.append(vibe_gen_offer)

    # Recommendation surface: one line, once per session, always last.
    if rec_surface_line:
        lines.append(rec_surface_line)

    # AI checks active note (Phase 3 Task 3.5): appended after rec surface
    # so findings remain the most prominent item in the context.
    if ai_checks_note:
        lines.append(ai_checks_note)

    # LLM-judge findings (Phase 3 Task 3.6): appended last so test failures
    # remain the most prominent part of the context.
    if llm_judge_lines:
        for judge_line in llm_judge_lines:
            lines.append(judge_line)

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
    """Write the full batch to ``latest.json`` + ``latest.html`` (best effort).

    Phase 1 wrote only the JSON report. Phase 2 Task 2.6 adds the
    HTML mirror so the ``/tailtest:report`` skill can point the
    user at a browser-openable file. Both writes happen in the
    same try/except block so a failure in one path does not
    cause the other to leak. The HTML writer is best-effort: if
    it fails we still want ``latest.json`` to land.
    """
    try:
        reports_dir = root / ".tailtest" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "latest.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist JSON report: %s", exc)
        return

    try:
        from tailtest.core.reporter.html import HTMLReporter

        HTMLReporter().write_report(batch, reports_dir)
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not persist HTML report: %s", exc)


# --- Security phase (Phase 2 Task 2.5) ---------------------------------


async def _run_security_phase(
    *,
    root: Path,
    changed_files: list[Path],
    config: Config,
    depth: DepthMode,
    run_id: str,
) -> list[Finding]:
    """Run the gitleaks + Semgrep + OSV scanners per the depth rules.

    Returns the merged list of security findings. Logs and swallows
    every exception so the hot loop never crashes because a scanner
    errored; the test-phase findings always make it back to the
    user regardless of what happened here.

    Depth gating:
    - ``off``: return empty immediately.
    - ``quick``: gitleaks only (fast, per-file scan).
    - ``standard`` / ``thorough`` / ``paranoid``: gitleaks + Semgrep
      on the changed files + OSV on any changed SCA manifest.
    """
    if depth == DepthMode.OFF:
        return []

    findings: list[Finding] = []

    # Secrets: cheap, runs at every non-off depth. Per-file scan.
    if config.security.secrets:
        try:
            runner = GitleaksRunner(root)
            if runner.is_available():
                findings.extend(await runner.scan(changed_files, run_id=run_id))
        except Exception as exc:  # noqa: BLE001, hot-loop-safe
            logger.warning("gitleaks scan failed: %s", exc)

    # SAST + SCA: only at standard+ depth. quick keeps the hot loop
    # snappy by scanning secrets alone.
    if depth == DepthMode.QUICK:
        return findings

    # Phase 2 Task 2.9: the SAST config is a nested type with an
    # `enabled` bool and a `ruleset` string. Prefer the explicit
    # `.enabled` attribute over bare truthiness so a disabled scanner
    # cannot accidentally pass the gate via SastConfig.__bool__.
    if config.security.sast.enabled:
        try:
            sast_runner = SemgrepRunner(root, ruleset=config.security.sast.ruleset)
            if sast_runner.is_available():
                findings.extend(await sast_runner.scan(changed_files, run_id=run_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("semgrep scan failed: %s", exc)

    if config.security.sca.enabled:
        sca_manifests = [f for f in changed_files if f.name in _SCA_MANIFEST_PARSERS]
        if sca_manifests:
            try:
                sca_findings = await _run_osv_for_manifest_edits(
                    root=root,
                    manifest_files=sca_manifests,
                    run_id=run_id,
                )
                findings.extend(sca_findings)
            except Exception as exc:  # noqa: BLE001
                logger.warning("osv scan failed: %s", exc)

    return findings


async def _run_osv_for_manifest_edits(
    *,
    root: Path,
    manifest_files: list[Path],
    run_id: str,
) -> list[Finding]:
    """Diff each edited manifest against its snapshot and query OSV.

    The first time a given manifest file is touched, its snapshot
    does not exist yet. We treat that as "old=empty", which means
    every dependency in the current manifest is considered "added"
    and gets queried. Subsequent hook runs compare the current
    manifest against the saved snapshot so only genuine additions +
    bumps surface.

    The snapshot is saved AFTER each call so the next hook run sees
    the current state as the new baseline.
    """
    snapshot_dir = root / ".tailtest" / "cache" / "manifests"

    all_findings: list[Finding] = []
    for manifest in manifest_files:
        parser = _SCA_MANIFEST_PARSERS.get(manifest.name)
        if parser is None:
            continue
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            logger.info("could not read manifest %s: %s", manifest, exc)
            continue

        new_refs = parser(text)
        old_refs = _load_manifest_snapshot(snapshot_dir, manifest.name)

        diff = diff_manifests(old_refs, new_refs)

        # Save the new state regardless of whether we find vulns so
        # the next run sees the current state as "old".
        _save_manifest_snapshot(snapshot_dir, manifest.name, new_refs)

        if not diff.changed_refs:
            continue

        lookup = OSVLookup(root)
        all_findings.extend(await lookup.check_manifest_diff(diff, run_id=run_id))

    return all_findings


def _load_manifest_snapshot(snapshot_dir: Path, filename: str) -> list[PackageRef]:
    """Load the saved manifest snapshot for ``filename`` or empty list.

    The snapshot is a JSON array of dicts with the ``PackageRef``
    field names. Any I/O or parse failure returns an empty list so
    the caller falls back to the "treat everything as added" branch.
    """
    path = snapshot_dir / f"{filename}.snap"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    refs: list[PackageRef] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        ecosystem = item.get("ecosystem")
        if not isinstance(name, str) or not isinstance(ecosystem, str):
            continue
        refs.append(
            PackageRef(
                name=name,
                version=str(item.get("version", "")),
                ecosystem=ecosystem,
                source_spec=str(item.get("source_spec", "")),
            )
        )
    return refs


def _save_manifest_snapshot(snapshot_dir: Path, filename: str, refs: list[PackageRef]) -> None:
    """Persist a manifest snapshot next to the OSV cache.

    Best effort; I/O failures are logged and swallowed because the
    hot loop must never break on a cache write error.
    """
    try:
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        path = snapshot_dir / f"{filename}.snap"
        payload = [
            {
                "name": r.name,
                "version": r.version,
                "ecosystem": r.ecosystem,
                "source_spec": r.source_spec,
            }
            for r in refs
        ]
        tmp = path.with_suffix(".snap.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.debug("manifest snapshot save failed: %s", exc)


def _build_summary_line(batch: FindingBatch, hook_duration_s: float) -> str:
    """Build the one-line status banner with test + security counts.

    Format: ``tailtest: <P>/<T> tests passed [· N failed] [· N skipped]
    [· N new security issue(s)] · <dur>s``.

    The security count is the number of non-baseline findings whose
    kind is SECRET, SAST, or SCA. We count after baseline application
    so the user only sees NEW security issues, matching the
    "test_failures that are new" semantics already in place.
    """
    from tailtest.core.findings.schema import FindingKind

    total = batch.tests_passed + batch.tests_failed + batch.tests_skipped
    summary = f"tailtest: {batch.tests_passed}/{total} tests passed"
    if batch.tests_failed:
        summary += f" · {batch.tests_failed} failed"
    if batch.tests_skipped:
        summary += f" · {batch.tests_skipped} skipped"

    security_kinds = {FindingKind.SECRET, FindingKind.SAST, FindingKind.SCA}
    new_security = sum(1 for f in batch.findings if f.kind in security_kinds and not f.in_baseline)
    if new_security > 0:
        label = "issue" if new_security == 1 else "issues"
        summary += f" · {new_security} new security {label}"

    summary += f" · {hook_duration_s:.1f}s"
    return summary


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
