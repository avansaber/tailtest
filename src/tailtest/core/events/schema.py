# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Event schema — the append-only bus between engine and dashboard.

Per Phase 1 Task 1.5.5 (backported from Phase 4 per the audit). Every
notable happening in tailtest — hook fires, scan runs, findings emitted,
config changes — gets one entry in this schema.

Format choice: JSON Lines (one JSON object per line, no comma-separation,
no enclosing array) so that append is O(1) and readers can tail the file
live.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    """Kinds of events that flow through the stream."""

    SESSION_START = "session_start"
    EDIT = "edit"
    SCAN = "scan"
    RUN = "run"
    FINDING = "finding"
    RECOMMENDATION = "recommendation"
    CONFIG_CHANGE = "config_change"
    GENERATION = "generation"
    DASHBOARD_CONNECTED = "dashboard_connected"


class Event(BaseModel):
    """One event on the tailtest stream.

    ``payload`` is a free-form dict whose shape depends on ``kind``. The
    dashboard dispatches on ``kind`` to render the right card.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: EventKind
    session_id: str
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_jsonl(self) -> str:
        """Serialize to a single JSONL line (no trailing newline)."""
        return self.model_dump_json()
