"""Recommendation schema for tailtest opportunity detection."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class RecommendationKind(StrEnum):
    install_tool = "install_tool"
    enable_depth = "enable_depth"
    add_test = "add_test"
    configure_runner = "configure_runner"
    enable_ai_checks = "enable_ai_checks"


class RecommendationPriority(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class Recommendation(BaseModel):
    """A single actionable recommendation produced by the engine or deep scan."""

    id: str = Field(
        default="",
        description="Stable hash of (kind, title, applies_to). Set automatically if empty.",
    )
    kind: RecommendationKind
    priority: RecommendationPriority
    title: str
    why: str = Field(description="One-sentence justification.")
    next_step: str = Field(description="Concrete action the user can take.")
    applies_to: str = Field(
        default="",
        description="Path or module this recommendation targets (empty = whole project).",
    )
    dismissible: bool = True
    dismissed_until: Optional[datetime] = None
    source: str = Field(
        default="rules",
        description="'rules' for deterministic rules, 'llm' for deep-scan recommendations.",
    )

    @model_validator(mode="after")
    def _set_id(self) -> "Recommendation":
        if not self.id:
            raw = f"{self.kind.value}:{self.title}:{self.applies_to}"
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return self

    @property
    def is_dismissed(self) -> bool:
        if self.dismissed_until is None:
            return False
        return datetime.now(tz=timezone.utc) < self.dismissed_until

    def dismiss(self, until: datetime) -> "Recommendation":
        """Return a new Recommendation with dismissed_until set."""
        return self.model_copy(update={"dismissed_until": until})
