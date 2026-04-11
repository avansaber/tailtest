# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.core.events — append-only event stream for the dashboard.

Every tailtest hook run, scan, finding emission, and config change writes
one JSON line to `.tailtest/events.jsonl`. The Phase 4 dashboard reads this
file to render the live timeline.

Writing the event stream is a Phase 1 task (see Task 1.5.5, backported from
Phase 4 per the audit) so that the dashboard has history the moment it
ships.
"""

from tailtest.core.events.schema import Event, EventKind
from tailtest.core.events.writer import EventWriter

__all__ = ["Event", "EventKind", "EventWriter"]
