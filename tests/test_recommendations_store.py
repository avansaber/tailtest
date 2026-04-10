"""Tests for DismissalStore (Phase 3 Task 3.2)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tailtest.core.recommendations import (
    DismissalStore,
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)

# --- Helpers -----------------------------------------------------------------


def _make_rec(title: str = "Add unit tests", applies_to: str = "") -> Recommendation:
    return Recommendation(
        kind=RecommendationKind.add_test,
        priority=RecommendationPriority.medium,
        title=title,
        why="Coverage is low.",
        next_step="Write more tests.",
        applies_to=applies_to,
    )


def _future(days: int = 7) -> datetime:
    return datetime.now(tz=UTC) + timedelta(days=days)


def _past(days: int = 1) -> datetime:
    return datetime.now(tz=UTC) - timedelta(days=days)


# --- load() ------------------------------------------------------------------


def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    """load() with no dismissed.json must return {} without raising."""
    store = DismissalStore(tmp_path)
    result = store.load()
    assert result == {}


def test_load_returns_empty_dict_when_file_empty_json_object(tmp_path: Path) -> None:
    """load() on an empty JSON object returns {}."""
    dismissed_dir = tmp_path / ".tailtest"
    dismissed_dir.mkdir()
    (dismissed_dir / "dismissed.json").write_text("{}", encoding="utf-8")

    store = DismissalStore(tmp_path)
    assert store.load() == {}


# --- dismiss() ---------------------------------------------------------------


def test_dismiss_writes_to_file(tmp_path: Path) -> None:
    """dismiss() must create the dismissed.json file with the correct entry."""
    store = DismissalStore(tmp_path)
    until = _future()
    store.dismiss("rec-abc", until)

    dismissed_path = tmp_path / ".tailtest" / "dismissed.json"
    assert dismissed_path.exists()
    data = json.loads(dismissed_path.read_text(encoding="utf-8"))
    assert "rec-abc" in data
    # The stored value must parse back to the same datetime.
    assert datetime.fromisoformat(data["rec-abc"]) == until


def test_dismiss_creates_directory_if_missing(tmp_path: Path) -> None:
    """dismiss() must create .tailtest/ if it does not exist."""
    store = DismissalStore(tmp_path)
    assert not (tmp_path / ".tailtest").exists()
    store.dismiss("rec-xyz", _future())
    assert (tmp_path / ".tailtest" / "dismissed.json").exists()


def test_dismiss_persists_across_store_instances(tmp_path: Path) -> None:
    """A dismissal written by one DismissalStore is readable by a second instance."""
    until = _future(days=14)
    store_a = DismissalStore(tmp_path)
    store_a.dismiss("rec-001", until)

    store_b = DismissalStore(tmp_path)
    loaded = store_b.load()
    assert "rec-001" in loaded
    assert loaded["rec-001"] == until


def test_dismiss_accumulates_entries(tmp_path: Path) -> None:
    """Multiple dismiss() calls accumulate entries rather than overwriting."""
    store = DismissalStore(tmp_path)
    until_a = _future(days=7)
    until_b = _future(days=14)

    store.dismiss("rec-aaa", until_a)
    store.dismiss("rec-bbb", until_b)

    loaded = store.load()
    assert "rec-aaa" in loaded
    assert "rec-bbb" in loaded
    assert loaded["rec-aaa"] == until_a
    assert loaded["rec-bbb"] == until_b


# --- apply() -----------------------------------------------------------------


def test_apply_marks_recs_in_store_as_dismissed(tmp_path: Path) -> None:
    """apply() populates dismissed_until for recs whose id is in the store."""
    rec = _make_rec()
    until = _future()
    store = DismissalStore(tmp_path)
    store.dismiss(rec.id, until)

    result = store.apply([rec])
    assert len(result) == 1
    assert result[0].dismissed_until == until
    assert result[0].is_dismissed is True


def test_apply_leaves_recs_not_in_store_untouched(tmp_path: Path) -> None:
    """apply() must not modify recs whose id is absent from the store."""
    rec = _make_rec()
    store = DismissalStore(tmp_path)

    result = store.apply([rec])
    assert len(result) == 1
    assert result[0].dismissed_until is None


def test_apply_handles_mixed_batch(tmp_path: Path) -> None:
    """apply() correctly handles a batch where some recs are dismissed and some are not."""
    rec_dismissed = _make_rec(title="Install coverage tool")
    rec_not = _make_rec(title="Configure runner")

    until = _future()
    store = DismissalStore(tmp_path)
    store.dismiss(rec_dismissed.id, until)

    result = store.apply([rec_dismissed, rec_not])
    assert result[0].is_dismissed is True
    assert result[1].is_dismissed is False


def test_apply_on_empty_store_leaves_all_recs_untouched(tmp_path: Path) -> None:
    """apply() with no dismissals stored must return recs unchanged."""
    recs = [_make_rec(title=f"Rec {i}") for i in range(3)]
    store = DismissalStore(tmp_path)
    result = store.apply(recs)
    assert all(r.dismissed_until is None for r in result)


# --- Error handling ----------------------------------------------------------


def test_load_logs_warning_on_corrupt_json(tmp_path: Path, caplog) -> None:
    """Corrupt JSON must not raise; load() returns {} and logs a warning."""
    dismissed_dir = tmp_path / ".tailtest"
    dismissed_dir.mkdir()
    (dismissed_dir / "dismissed.json").write_text("not valid json {{{", encoding="utf-8")

    store = DismissalStore(tmp_path)
    with caplog.at_level(logging.WARNING):
        result = store.load()

    assert result == {}
    assert any("Could not read" in r.message for r in caplog.records)


def test_load_skips_invalid_timestamp_entries(tmp_path: Path) -> None:
    """Entries with unparseable timestamps are silently skipped."""
    dismissed_dir = tmp_path / ".tailtest"
    dismissed_dir.mkdir()
    data = {
        "rec-good": _future().isoformat(),
        "rec-bad": "not-a-timestamp",
    }
    (dismissed_dir / "dismissed.json").write_text(json.dumps(data), encoding="utf-8")

    store = DismissalStore(tmp_path)
    loaded = store.load()
    assert "rec-good" in loaded
    assert "rec-bad" not in loaded


def test_missing_dismissed_json_is_not_an_error(tmp_path: Path) -> None:
    """load() with no file must return {} without logging anything."""
    store = DismissalStore(tmp_path)
    # Must not raise.
    result = store.load()
    assert result == {}


def test_atomic_write_original_unchanged_on_write_failure(tmp_path: Path, caplog) -> None:
    """If the atomic write fails (OSError), the original dismissed.json is preserved."""
    # Write an initial valid entry.
    store = DismissalStore(tmp_path)
    original_until = _future(days=3)
    store.dismiss("rec-original", original_until)

    dismissed_path = tmp_path / ".tailtest" / "dismissed.json"
    original_content = dismissed_path.read_text(encoding="utf-8")

    # Simulate OSError during the tmp write step.
    with (
        patch("pathlib.Path.write_text", side_effect=OSError("disk full")),
        caplog.at_level(logging.WARNING),
    ):
        store.dismiss("rec-new", _future(days=10))

    # The original file must be intact.
    assert dismissed_path.read_text(encoding="utf-8") == original_content
    assert any("Could not write" in r.message for r in caplog.records)
