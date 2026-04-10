"""SessionStart hook runtime (Phase 1 Task 1.6).

Runs once at Claude Code session startup. Responsibilities per the
Task 1.6 spec:

1. Parse the session_id from the stdin payload (best effort; empty is
   fine, we fall back to "unknown").
2. Check for ``.tailtest/config.yaml``. If missing, create it with
   default values via ConfigLoader.ensure_default.
3. Run the shallow project scan and save the profile to
   ``.tailtest/profile.json``.
4. Handle empty projects (audit gap #1): if the scan finds no source
   files, skip the rest of the bootstrap and emit a gentle message.
5. Handle scan failures (audit gap #2b): any scanner exception is
   caught, logged at warning level, and surfaces as an informative
   message without crashing the session.
6. Reset the session-state.json file for the new session so the
   auto-offer debounce cache does not carry offers from a prior
   session. This also writes the new session_id to the state file.
7. Emit a single-line ``hookSpecificOutput`` / ``additionalContext``
   envelope describing what tailtest sees and what depth mode is in
   effect.

The repo-root ``hooks/session_start.py`` file is a thin shim that
reads stdin, calls this library, prints the result, and exits 0.

This runtime does NOT warm the TIA cache in Phase 1 (the plan says
to, but PythonRunner does not yet persist a TIA cache file on disk
so there is nothing to warm). When Phase 1 adds a persistent TIA
cache, a call goes here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tailtest.core.config import ConfigLoader
from tailtest.core.recommendations.store import DismissalStore
from tailtest.core.recommender.engine import RecommendationEngine
from tailtest.core.scan import ProjectScanner
from tailtest.core.scan.profile import AISurface, ScanStatus
from tailtest.core.session_state import SessionState, save_session_state

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionStartResult:
    """Return value of the SessionStart runtime.

    ``stdout_json`` is the JSON string the shim should print (the
    hookSpecificOutput envelope), or None if the hook should emit
    nothing. ``reason`` is a diagnostic for logs.
    """

    stdout_json: str | None
    reason: str


async def run(
    stdin_text: str,
    *,
    project_root: Path | None = None,
) -> SessionStartResult:
    """Bootstrap a tailtest session and emit the init message.

    Parameters mirror ``tailtest.hook.post_tool_use.run`` for
    consistency. ``stdin_text`` is the raw JSON from the hook
    process's stdin; ``project_root`` defaults to cwd.
    """
    root = (project_root or Path.cwd()).resolve()

    # Reap expired LLM-judge cache files from prior sessions.
    _reap_expired_judgments(root)

    payload = _parse_stdin(stdin_text)
    session_id = _extract_session_id(payload)

    tailtest_dir = root / ".tailtest"

    # Load config or bootstrap defaults. This always succeeds (the
    # loader returns defaults on any parse error and logs a warning).
    loader = ConfigLoader(tailtest_dir)
    config = loader.ensure_default()

    # Run the shallow scan. Catch every exception so an empty or
    # broken project cannot crash the session start path.
    scan_status: str = "ok"
    scan_message: str = ""
    profile = None
    try:
        scanner = ProjectScanner(root)
        profile = scanner.scan_shallow()
        # Propagate ai_checks_enabled from config into the profile so
        # downstream consumers (hooks, engine) see the user's decision
        # without re-reading the config file themselves.
        profile = profile.model_copy(update={"ai_checks_enabled": config.ai_checks_enabled})
        scanner.save_profile(profile)
        if profile.scan_status == ScanStatus.FAILED:
            scan_status = "failed"
            scan_message = "scan failed, run tailtest doctor to debug"
    except Exception as exc:  # noqa: BLE001
        logger.warning("SessionStart scan failed: %s", exc)
        scan_status = "failed"
        scan_message = f"scan exception: {exc}"

    # Reset the session-state cache for the new session id so auto-
    # offer debouncing does not carry offers from a prior session.
    new_state = SessionState(session_id=session_id or "unknown")
    save_session_state(tailtest_dir, new_state)

    # Build the one-line additionalContext envelope.
    if scan_status == "failed":
        message = f"tailtest: {scan_message}"
    elif profile is None or _profile_is_empty(profile):
        # Audit gap #1: empty project. Be gentle; the user may not
        # have written code yet.
        message = "tailtest: ready, I will start helping once you have code to test."
    else:
        primary = profile.primary_language or "unknown"
        runners_list = (
            [f.name for f in profile.frameworks_detected] if profile.frameworks_detected else []
        )
        runner_part = runners_list[0] if runners_list else "no test runner detected"
        message = (
            f"tailtest: initialized in {config.depth.value} mode, "
            f"{primary} project, {runner_part}. Run /tailtest:status for options."
        )

    # Append a high-priority recommendation count if any exist.
    # One line max; never shows full recommendation text (noise discipline).
    # Cap: skip this line if the current message is already long (rough
    # 500-token guard -- 500 tokens * ~4 chars/token = 2000 chars).
    if profile is not None and not _profile_is_empty(profile) and scan_status != "failed":
        rec_line = _build_rec_count_line(profile, tailtest_dir)
        if rec_line and len(message.encode("utf-8")) < 1800:
            message = f"{message}\n{rec_line}"

    # One-time AI-agent offer (Phase 3 Task 3.5). Fires when the project
    # is detected as an AI agent and the user has not yet decided whether
    # to enable AI-specific checks. The flag file persists permanently so
    # the offer fires at most once total (not once per session).
    if profile is not None and not _profile_is_empty(profile) and scan_status != "failed":
        ai_offer = _maybe_build_ai_offer(profile, tailtest_dir)
        if ai_offer:
            message = f"{message}\n{ai_offer}"

    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": message,
        }
    }
    return SessionStartResult(stdout_json=json.dumps(envelope), reason=scan_status)


# --- Helpers ------------------------------------------------------------


def _reap_expired_judgments(project_root: Path) -> None:
    """Delete expired judgment cache files (older than 24h)."""
    cache_dir = project_root / ".tailtest" / "cache" / "judgments"
    if not cache_dir.exists():
        return
    cutoff = time.time() - 86400
    try:
        for p in cache_dir.glob("*.json"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass


def _maybe_build_ai_offer(profile: Any, tailtest_dir: Path) -> str | None:
    """Return a one-time AI-agent offer string, or None.

    Fires when ALL of these are true:
    - profile.ai_surface == AISurface.AGENT
    - profile.ai_checks_enabled is None (user has not decided)
    - .tailtest/ai_offer_shown.flag does NOT exist

    Writes the flag on first call so the offer never repeats. All
    failures are swallowed and logged -- this path must never raise.
    """
    try:
        ai_surface = getattr(profile, "ai_surface", None)
        ai_checks_enabled = getattr(profile, "ai_checks_enabled", None)

        if ai_surface != AISurface.AGENT:
            return None
        if ai_checks_enabled is not None:
            return None

        flag_path = tailtest_dir / "ai_offer_shown.flag"
        if flag_path.exists():
            return None

        # Write the flag so subsequent sessions never show the offer again.
        try:
            tailtest_dir.mkdir(parents=True, exist_ok=True)
            flag_path.write_text("", encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not write ai_offer_shown.flag: %s", exc)

        return (
            "tailtest: AI agent detected. Want AI-specific checks (LLM-judge assertions)"
            " at thorough depth?\n"
            "  Run /tailtest accept-ai-checks to enable, or /tailtest dismiss-ai-checks"
            " to skip.\n"
            "  These checks only run when scan_mode is set to \"thorough\" or above."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI offer check failed: %s", exc)
        return None


def _build_rec_count_line(profile: Any, tailtest_dir: Path) -> str | None:
    """Return a one-line high-priority recommendation summary, or None.

    Runs the rules-based engine, applies dismissals, and counts HIGH-priority
    active recommendations. Returns None when the count is zero or on any
    failure (this path must never raise).
    """
    try:
        engine = RecommendationEngine()
        recs = engine.compute(profile)
        store = DismissalStore(tailtest_dir.parent)
        recs = store.apply(recs)
        high_active = [r for r in recs if r.priority == "high" and not r.is_dismissed]
        if not high_active:
            return None
        count = len(high_active)
        noun = "recommendation" if count == 1 else "recommendations"
        return (
            f"tailtest: {count} high-priority {noun} -- run /tailtest to see them."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("SessionStart rec count failed: %s", exc)
        return None


def _parse_stdin(stdin_text: str) -> dict[str, Any] | None:
    """Parse the SessionStart stdin payload. Returns None on any failure.

    Same defensive-return-None pattern as the PostToolUse hook. A
    malformed or empty stdin must not crash the session start.
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


def _extract_session_id(payload: dict[str, Any] | None) -> str | None:
    """Pull session_id out of the payload, or return None."""
    if payload is None:
        return None
    session_id = payload.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def _profile_is_empty(profile: Any) -> bool:
    """Return True when the scanner found no code to care about.

    A project is "empty" for SessionStart purposes when:
    - primary_language is None or empty, AND
    - languages map is empty or sums to zero files

    Per audit gap #1, we treat this as "come back when there is code",
    not an error.
    """
    primary = getattr(profile, "primary_language", None)
    languages = getattr(profile, "languages", {}) or {}
    total_language_files = sum(v for v in languages.values() if isinstance(v, int))
    return not primary and total_language_files == 0
