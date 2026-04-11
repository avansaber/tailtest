# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Event stream writer — append-only JSONL file at .tailtest/events.jsonl.

Phase 1 Task 1.5.5. The writer is used by every hook and engine module that
wants to emit an event. Rotation fires at 50 MB: the active file is gzipped
to `events-<timestamp>.jsonl.gz` and a fresh file replaces it.

Writing is synchronous and atomic: each call does one ``open(..., "a")``
with context manager semantics so partial writes can't corrupt the stream.
For high-throughput phases, callers can batch by constructing events
themselves and then calling `write_many`.
"""

from __future__ import annotations

import gzip
import shutil
from datetime import UTC, datetime
from pathlib import Path

from tailtest.core.events.schema import Event

ROTATE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


class EventWriter:
    """Append-only event stream writer.

    Parameters
    ----------
    tailtest_dir:
        The project's ``.tailtest/`` directory. The stream lives at
        ``<tailtest_dir>/events.jsonl``. Parent directories are created on
        first write if they don't exist.
    rotate_size_bytes:
        File size threshold for rotation. Defaults to 50 MB. Tests can
        override to exercise rotation with small files.
    """

    def __init__(
        self,
        tailtest_dir: Path,
        *,
        rotate_size_bytes: int = ROTATE_SIZE_BYTES,
    ) -> None:
        self._tailtest_dir = tailtest_dir
        self._rotate_size_bytes = rotate_size_bytes

    @property
    def events_path(self) -> Path:
        return self._tailtest_dir / "events.jsonl"

    def append(self, event: Event) -> None:
        """Append a single event to the stream. Rotates if needed."""
        self._tailtest_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        line = event.to_jsonl()
        with self.events_path.open("a", encoding="utf-8") as fp:
            fp.write(line)
            fp.write("\n")

    def write_many(self, events: list[Event]) -> None:
        """Append a batch of events in one open call (more efficient)."""
        if not events:
            return
        self._tailtest_dir.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        with self.events_path.open("a", encoding="utf-8") as fp:
            for event in events:
                fp.write(event.to_jsonl())
                fp.write("\n")

    def read_all(self) -> list[Event]:
        """Read every event currently in the active file.

        Does NOT read rotated archives. Callers that need history beyond
        the current file must walk ``rotated_archives()`` separately.
        """
        if not self.events_path.exists():
            return []
        events: list[Event] = []
        with self.events_path.open("r", encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                events.append(Event.model_validate_json(line))
        return events

    def rotated_archives(self) -> list[Path]:
        """List the rotated `.jsonl.gz` archives under the tailtest dir."""
        if not self._tailtest_dir.exists():
            return []
        return sorted(self._tailtest_dir.glob("events-*.jsonl.gz"))

    def _rotate_if_needed(self) -> None:
        """Rotate the active file if it exceeds the size threshold."""
        if not self.events_path.exists():
            return
        try:
            size = self.events_path.stat().st_size
        except OSError:
            return
        if size < self._rotate_size_bytes:
            return
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = self._tailtest_dir / f"events-{timestamp}.jsonl.gz"
        with self.events_path.open("rb") as src, gzip.open(archive, "wb") as dst:
            shutil.copyfileobj(src, dst)
        self.events_path.unlink()
