# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Session-scoped state for debouncing hook suggestions (Phase 1 Task 1.5a).

Stored at ``.tailtest/session-state.json`` and read + rewritten by the
PostToolUse hook on every invocation. The file tracks which auto-offer
suggestions have already been surfaced in the current Claude Code
session so we do not re-emit the same "consider running /tailtest:gen"
twice for the same symbol.

File format (JSON, sorted keys for reproducibility):

    {
      "schema_version": 1,
      "session_id": "opaque string from Claude Code",
      "seen_offers": [
        {"file": "src/foo.py", "symbol": "bar", "first_seen_iso": "..."},
        ...
      ]
    }

On disk size is tiny, writes are synchronous and atomic (temp file
plus rename). The session_id comes from the hook payload; when the
caller cannot provide one, we use the literal string ``"unknown"`` and
log a debug message.

Why a file and not a process-memory cache: PostToolUse hooks run as
fresh subprocesses per Claude edit, so any in-process state would die
between invocations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SESSION_STATE_SCHEMA_VERSION = 1


@dataclass
class SeenOffer:
    """One auto-offer suggestion that was surfaced earlier in the session."""

    file: str
    symbol: str
    first_seen_iso: str

    def to_dict(self) -> dict[str, str]:
        return {
            "file": self.file,
            "symbol": self.symbol,
            "first_seen_iso": self.first_seen_iso,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SeenOffer | None:
        file = data.get("file")
        symbol = data.get("symbol")
        first_seen = data.get("first_seen_iso")
        if not isinstance(file, str) or not isinstance(symbol, str):
            return None
        if not isinstance(first_seen, str):
            first_seen = ""
        return cls(file=file, symbol=symbol, first_seen_iso=first_seen)


@dataclass
class SessionState:
    """The full session-state document, loaded from or about to be saved."""

    session_id: str
    seen_offers: list[SeenOffer] = field(default_factory=list)
    schema_version: int = SESSION_STATE_SCHEMA_VERSION

    # --- Public API ---

    def has_seen(self, file: str, symbol: str) -> bool:
        """Return True if the `(file, symbol)` pair has already been offered."""
        return any(entry.file == file and entry.symbol == symbol for entry in self.seen_offers)

    def mark_seen(self, file: str, symbol: str) -> None:
        """Record that we offered generation for `(file, symbol)`.

        Idempotent: calling twice for the same pair is a no-op. The
        stored timestamp reflects the first time the pair was seen.
        """
        if self.has_seen(file, symbol):
            return
        self.seen_offers.append(
            SeenOffer(
                file=file,
                symbol=symbol,
                first_seen_iso=datetime.now(UTC).isoformat(),
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "seen_offers": [entry.to_dict() for entry in self.seen_offers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        session_id = data.get("session_id") or "unknown"
        raw_offers = data.get("seen_offers") or []
        offers: list[SeenOffer] = []
        if isinstance(raw_offers, list):
            for raw in raw_offers:
                if isinstance(raw, dict):
                    entry = SeenOffer.from_dict(raw)
                    if entry is not None:
                        offers.append(entry)
        return cls(session_id=str(session_id), seen_offers=offers)


# --- Filesystem helpers ------------------------------------------------


def _session_state_path(tailtest_dir: Path) -> Path:
    return tailtest_dir / "session-state.json"


def load_session_state(
    tailtest_dir: Path,
    *,
    current_session_id: str | None = None,
) -> SessionState:
    """Load the session state from disk, or return a fresh one.

    If the on-disk file is missing, malformed, or belongs to a DIFFERENT
    session_id than the one passed in, the function returns a fresh
    SessionState bound to the current session. This is the mechanism
    that resets the "already offered" list when Claude Code starts a
    new session.
    """
    path = _session_state_path(tailtest_dir)
    if not path.exists():
        return SessionState(session_id=current_session_id or "unknown")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.info("session state unreadable (%s); using fresh state", exc)
        return SessionState(session_id=current_session_id or "unknown")

    if not isinstance(data, dict):
        return SessionState(session_id=current_session_id or "unknown")

    loaded = SessionState.from_dict(data)

    # Session rollover: if the caller knows the current session id and
    # it does not match the stored one, start fresh. A missing caller
    # session id means "caller did not look", preserve whatever was on
    # disk.
    if current_session_id and loaded.session_id != current_session_id:
        return SessionState(session_id=current_session_id)

    return loaded


def save_session_state(tailtest_dir: Path, state: SessionState) -> None:
    """Atomically write the session state to disk.

    Writes to a temp file then renames so a crash during write cannot
    leave a partially-serialized JSON file. Best-effort: any OS error
    is logged at debug level and swallowed, because the hook must
    never crash on a filesystem hiccup.
    """
    try:
        tailtest_dir.mkdir(parents=True, exist_ok=True)
        path = _session_state_path(tailtest_dir)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        logger.debug("could not save session state: %s", exc)
