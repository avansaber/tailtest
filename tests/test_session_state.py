"""Tests for session state persistence (Phase 1 Task 1.5a debounce)."""

from __future__ import annotations

import json
from pathlib import Path

from tailtest.core.session_state import (
    SESSION_STATE_SCHEMA_VERSION,
    SeenOffer,
    SessionState,
    load_session_state,
    save_session_state,
)

# --- SessionState dataclass surface -----------------------------------


def test_has_seen_false_when_empty() -> None:
    state = SessionState(session_id="s1")
    assert state.has_seen("foo.py", "bar") is False


def test_mark_seen_adds_entry() -> None:
    state = SessionState(session_id="s1")
    state.mark_seen("src/foo.py", "add")
    assert state.has_seen("src/foo.py", "add") is True
    assert len(state.seen_offers) == 1


def test_mark_seen_is_idempotent() -> None:
    state = SessionState(session_id="s1")
    state.mark_seen("src/foo.py", "add")
    state.mark_seen("src/foo.py", "add")
    state.mark_seen("src/foo.py", "add")
    assert len(state.seen_offers) == 1


def test_mark_seen_tracks_different_symbols_separately() -> None:
    state = SessionState(session_id="s1")
    state.mark_seen("src/foo.py", "add")
    state.mark_seen("src/foo.py", "sub")
    assert len(state.seen_offers) == 2


def test_to_dict_round_trip() -> None:
    original = SessionState(session_id="s1")
    original.mark_seen("src/foo.py", "add")
    original.mark_seen("src/bar.py", "sub")
    restored = SessionState.from_dict(original.to_dict())
    assert restored.session_id == "s1"
    assert restored.has_seen("src/foo.py", "add") is True
    assert restored.has_seen("src/bar.py", "sub") is True


def test_from_dict_tolerates_missing_fields() -> None:
    state = SessionState.from_dict({})
    assert state.session_id == "unknown"
    assert state.seen_offers == []


def test_from_dict_ignores_malformed_offer_entries() -> None:
    data = {
        "session_id": "s1",
        "seen_offers": [
            {"file": "ok.py", "symbol": "x", "first_seen_iso": "2026-04-09T00:00:00Z"},
            {"file": 42, "symbol": "y"},  # malformed, should be dropped
            "not a dict",
            {"file": "only-file.py"},  # missing symbol, dropped
        ],
    }
    state = SessionState.from_dict(data)
    assert len(state.seen_offers) == 1
    assert state.seen_offers[0].file == "ok.py"


def test_schema_version_is_1() -> None:
    assert SESSION_STATE_SCHEMA_VERSION == 1


# --- File persistence --------------------------------------------------


def test_load_session_state_returns_fresh_when_file_missing(tmp_path: Path) -> None:
    state = load_session_state(tmp_path, current_session_id="session-abc")
    assert state.session_id == "session-abc"
    assert state.seen_offers == []


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    state = SessionState(session_id="session-123")
    state.mark_seen("src/foo.py", "add")
    save_session_state(tmp_path, state)

    reloaded = load_session_state(tmp_path, current_session_id="session-123")
    assert reloaded.session_id == "session-123"
    assert reloaded.has_seen("src/foo.py", "add") is True


def test_load_resets_state_on_session_id_change(tmp_path: Path) -> None:
    """When the current session id differs from the stored one, start fresh."""
    old = SessionState(session_id="old-session")
    old.mark_seen("src/foo.py", "add")
    save_session_state(tmp_path, old)

    reloaded = load_session_state(tmp_path, current_session_id="new-session")
    assert reloaded.session_id == "new-session"
    assert reloaded.seen_offers == []


def test_load_preserves_state_when_caller_passes_no_session_id(tmp_path: Path) -> None:
    """If the caller does not pass a session id, keep whatever was on disk."""
    state = SessionState(session_id="existing-session")
    state.mark_seen("src/foo.py", "add")
    save_session_state(tmp_path, state)

    reloaded = load_session_state(tmp_path, current_session_id=None)
    assert reloaded.session_id == "existing-session"
    assert reloaded.has_seen("src/foo.py", "add") is True


def test_load_returns_fresh_when_file_is_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "session-state.json"
    path.write_text("{not json", encoding="utf-8")
    state = load_session_state(tmp_path, current_session_id="session-xyz")
    assert state.session_id == "session-xyz"
    assert state.seen_offers == []


def test_load_returns_fresh_when_file_is_not_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "session-state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    state = load_session_state(tmp_path, current_session_id="session-xyz")
    assert state.seen_offers == []


def test_save_is_atomic_via_tmp_file(tmp_path: Path) -> None:
    """Save writes via a `.tmp` file and renames, so the target is always valid."""
    state = SessionState(session_id="s1")
    state.mark_seen("src/foo.py", "add")
    save_session_state(tmp_path, state)

    # After save, only the final file exists (no leftover .tmp).
    assert (tmp_path / "session-state.json").exists()
    assert not (tmp_path / "session-state.json.tmp").exists()


def test_save_swallows_os_errors(tmp_path: Path) -> None:
    """A filesystem failure must not crash the hook."""
    state = SessionState(session_id="s1")
    # Point at a path whose parent is a file, not a directory, so the
    # underlying mkdir / write fails. save_session_state must swallow.
    bad_parent = tmp_path / "not-a-dir"
    bad_parent.write_text("blocking file")
    save_session_state(bad_parent, state)  # should not raise


# --- SeenOffer dataclass -----------------------------------------------


def test_seen_offer_to_dict_has_three_fields() -> None:
    entry = SeenOffer(
        file="src/foo.py",
        symbol="add",
        first_seen_iso="2026-04-09T00:00:00+00:00",
    )
    out = entry.to_dict()
    assert set(out.keys()) == {"file", "symbol", "first_seen_iso"}
