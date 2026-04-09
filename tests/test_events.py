"""Tests for the event stream schema + writer (Phase 1 Task 1.5.5)."""

from __future__ import annotations

from pathlib import Path

from tailtest.core.events import Event, EventKind, EventWriter


def _make_event(session_id: str = "sess-1", kind: EventKind = EventKind.EDIT) -> Event:
    return Event(
        session_id=session_id,
        kind=kind,
        payload={"file": "src/foo.py", "tool": "Edit"},
    )


def test_event_has_default_id_and_timestamp() -> None:
    event = _make_event()
    assert event.id  # non-empty hex uuid
    assert len(event.id) == 32
    assert event.timestamp.tzinfo is not None


def test_event_to_jsonl_roundtrip() -> None:
    event = _make_event()
    line = event.to_jsonl()
    assert "\n" not in line
    restored = Event.model_validate_json(line)
    assert restored == event


def test_event_kind_has_expected_values() -> None:
    expected = {
        "session_start",
        "edit",
        "scan",
        "run",
        "finding",
        "recommendation",
        "config_change",
        "generation",
        "dashboard_connected",
    }
    assert {k.value for k in EventKind} == expected


def test_event_writer_append_creates_file(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)

    writer.append(_make_event())

    assert writer.events_path.exists()
    assert writer.events_path.parent == tailtest_dir
    # One line in file
    content = writer.events_path.read_text().strip()
    assert content.count("\n") == 0
    assert content.startswith("{")


def test_event_writer_append_many(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)

    events = [_make_event(kind=EventKind.EDIT) for _ in range(5)]
    writer.write_many(events)

    lines = writer.events_path.read_text().strip().split("\n")
    assert len(lines) == 5
    for line in lines:
        restored = Event.model_validate_json(line)
        assert restored.kind == EventKind.EDIT


def test_event_writer_read_all(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)

    # Different kinds so we can verify ordering
    writer.append(_make_event(kind=EventKind.SESSION_START))
    writer.append(_make_event(kind=EventKind.EDIT))
    writer.append(_make_event(kind=EventKind.RUN))

    events = writer.read_all()
    assert len(events) == 3
    assert [e.kind for e in events] == [EventKind.SESSION_START, EventKind.EDIT, EventKind.RUN]


def test_event_writer_read_all_empty_file(tmp_path: Path) -> None:
    """Reading a non-existent stream returns an empty list, not an error."""
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)
    assert writer.read_all() == []


def test_event_writer_rotates_at_threshold(tmp_path: Path) -> None:
    """When the active file exceeds rotate_size_bytes, it gzips and resets."""
    tailtest_dir = tmp_path / ".tailtest"
    # Tiny threshold so we can trigger rotation with a handful of events.
    writer = EventWriter(tailtest_dir, rotate_size_bytes=100)

    # First few events — fits under the threshold
    writer.append(_make_event(kind=EventKind.EDIT))
    assert writer.events_path.exists()
    assert len(writer.rotated_archives()) == 0

    # Append enough events to blow past 100 bytes
    for _ in range(20):
        writer.append(_make_event(kind=EventKind.EDIT))

    # After rotation: the active file exists but is smaller than the
    # single line that just tripped rotation (the pre-rotation content
    # got gzipped). There should be at least one rotated archive.
    assert len(writer.rotated_archives()) >= 1
    archive = writer.rotated_archives()[0]
    assert archive.suffix == ".gz"
    assert archive.name.startswith("events-")


def test_event_writer_ignores_empty_batch(tmp_path: Path) -> None:
    """write_many([]) must not create or modify the events file."""
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)
    writer.write_many([])
    assert not writer.events_path.exists()


def test_event_writer_read_all_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines in the stream (e.g. from accidental appends) must be skipped."""
    tailtest_dir = tmp_path / ".tailtest"
    writer = EventWriter(tailtest_dir)

    writer.append(_make_event())
    # Manually inject a blank line
    with writer.events_path.open("a") as fp:
        fp.write("\n\n")
    writer.append(_make_event())

    events = writer.read_all()
    assert len(events) == 2
