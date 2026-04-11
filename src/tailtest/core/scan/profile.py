# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""ProjectProfile — the data shape the scanner produces (Phase 1 Task 1.12a).

The profile is the canonical source of truth for "what is this project?" —
every downstream feature reads from it. See ADR 0010 for the full rationale
and schema specification.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SCAN_SCHEMA_VERSION = 1


# --- Enums ----------------------------------------------------------------


class AISurface(StrEnum):
    """What kind of AI code the project contains."""

    NONE = "none"
    UTILITY = "utility"  # uses an LLM API for one-off tasks (embeddings, etc.)
    AGENT = "agent"  # has an agent loop, tool use, or multi-turn reasoning
    FRAMEWORK = "framework"  # IS an agent framework (like langchain itself)


class AIConfidence(StrEnum):
    """How sure the scanner is about its AI-surface determination."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PlanFileKind(StrEnum):
    """Kinds of project-level planning / instruction files the scanner recognizes."""

    CLAUDE_CODE_INSTRUCTIONS = "claude-code-instructions"  # CLAUDE.md
    AGENT_INVENTORY = "agent-inventory"  # AGENTS.md
    PROJECT_PLAN = "project-plan"  # docs/plan.md, PLAN.md
    ROADMAP = "roadmap"  # ROADMAP.md
    README = "readme"  # README.md, README.rst
    CURSOR_RULES = "cursor-rules"  # .cursor/ or .cursorrules
    CLAUDE_CONFIG = "claude-config"  # .claude/ directory


class InfrastructureKind(StrEnum):
    """Kinds of infrastructure artifacts the scanner recognizes."""

    DOCKER = "docker"
    DOCKER_COMPOSE = "docker-compose"
    KUBERNETES = "kubernetes"
    TERRAFORM = "terraform"
    CI = "ci"
    ENV_CONFIG = "env-config"


class ScanStatus(StrEnum):
    """High-level state of the last scan run."""

    OK = "ok"
    PARTIAL = "partial"  # scan hit the file-count ceiling and degraded
    FAILED = "failed"


# --- Detected-item models -------------------------------------------------


class DetectedFramework(BaseModel):
    """A framework (web, agent, ML) found in the project."""

    model_config = ConfigDict(extra="forbid")

    name: str
    confidence: AIConfidence = AIConfidence.HIGH
    source: str = Field(description="How it was detected, e.g. 'pyproject.toml' or 'src/main.py:3'")
    category: str = Field(default="", description="web, agent, ml, test, other")


class DetectedInfrastructure(BaseModel):
    """An infrastructure artifact (Dockerfile, CI config, etc.) found in the project."""

    model_config = ConfigDict(extra="forbid")

    kind: InfrastructureKind
    file: Path


class DetectedPlanFile(BaseModel):
    """A project-level planning file found in the root or docs directory."""

    model_config = ConfigDict(extra="forbid")

    path: Path
    kind: PlanFileKind


class DetectedRunner(BaseModel):
    """A test runner the scanner believes is configured for this project."""

    model_config = ConfigDict(extra="forbid")

    name: str  # pytest, jest, vitest, cargo-test, phpunit, etc.
    language: str
    config_file: Path | None = None
    tests_dir: Path | None = None


class DirectoryClassification(BaseModel):
    """Buckets of directories found in the project."""

    model_config = ConfigDict(extra="forbid")

    source: list[Path] = Field(default_factory=list)
    tests: list[Path] = Field(default_factory=list)
    docs: list[Path] = Field(default_factory=list)
    examples: list[Path] = Field(default_factory=list)
    generated: list[Path] = Field(default_factory=list)
    ignored: list[Path] = Field(default_factory=list)


# --- Agent entry point ----------------------------------------------------


class EntryPoint(BaseModel):
    """A detected (or config-declared) agent entry point.

    Produced by Phase 6 Task 6.3 detection and stored in the profile.
    The red-team runner reads these to know which code to analyze.
    """

    model_config = ConfigDict(extra="ignore")

    file: Path
    function: str
    language: str  # "python" | "typescript" | "rust"
    confidence: str  # "high" | "medium" | "low"
    framework: str | None = None


# --- Main profile ---------------------------------------------------------


class ProjectProfile(BaseModel):
    """The canonical 'what is this project?' record.

    Produced by `ProjectScanner.scan_shallow()` (always cheap, no LLM)
    or `ProjectScanner.scan_deep()` (Phase 3, adds `llm_summary`).
    """

    model_config = ConfigDict(extra="ignore")

    # Schema version + status
    schema_version: int = Field(default=SCAN_SCHEMA_VERSION)
    scan_status: ScanStatus = ScanStatus.OK
    scan_error: str | None = None

    # When + where
    scanned_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    root: Path
    content_hash: str = Field(default="", description="Hash of structural indicator files")

    # File counts + performance markers
    total_files_walked: int = 0
    scan_duration_ms: float = 0.0
    scan_mode: str = "shallow"  # "shallow" | "deep" | "partial"

    # Language composition (file count by language)
    languages: dict[str, int] = Field(default_factory=dict)
    primary_language: str | None = None

    # Detected capabilities
    runners_detected: list[DetectedRunner] = Field(default_factory=list)
    frameworks_detected: list[DetectedFramework] = Field(default_factory=list)
    infrastructure_detected: list[DetectedInfrastructure] = Field(default_factory=list)
    plan_files_detected: list[DetectedPlanFile] = Field(default_factory=list)

    # Directory classification
    directories: DirectoryClassification = Field(default_factory=DirectoryClassification)

    # AI-surface determination
    ai_surface: AISurface = AISurface.NONE
    ai_confidence: AIConfidence = AIConfidence.LOW
    ai_signals: list[str] = Field(default_factory=list)

    # Vibe-coded heuristic (cheap filesystem check)
    likely_vibe_coded: bool = False
    vibe_coded_signals: list[str] = Field(default_factory=list)

    # Phase 3+ deep scan output
    llm_summary: str | None = None

    # Phase 3 Task 3.2: serialized Recommendation objects from deep scan
    recommendations: list[dict] = Field(default_factory=list)

    # Phase 3 Task 3.5: propagated from config.ai_checks_enabled.
    # None = unset (user has not decided); True = enabled; False = dismissed.
    ai_checks_enabled: bool | None = None

    # Phase 6 Task 6.3: detected agent entry points for red-team runner.
    agent_entry_points: list[EntryPoint] = Field(default_factory=list)

    # --- Convenience helpers ---

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize to JSON for persistence at `.tailtest/profile.json`."""
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, text: str) -> ProjectProfile:
        return cls.model_validate_json(text)

    def has_plan_file(self, kind: PlanFileKind) -> bool:
        return any(p.kind == kind for p in self.plan_files_detected)

    def has_framework(self, name: str) -> bool:
        return any(f.name == name for f in self.frameworks_detected)

    def has_runner(self, language: str) -> bool:
        return any(r.language == language for r in self.runners_detected)

    def summary_line(self) -> str:
        """One-line human-readable summary for status commands."""
        if self.scan_status == ScanStatus.FAILED:
            return f"scan failed: {self.scan_error or 'unknown error'}"

        lang = self.primary_language or "unknown language"
        file_count = self.total_files_walked
        runners = ", ".join(r.name for r in self.runners_detected) or "no runners"
        ai = f" · {self.ai_surface.value} AI" if self.ai_surface != AISurface.NONE else ""
        vibe = " · vibe-coded" if self.likely_vibe_coded else ""
        return f"{lang} · {file_count} files · {runners}{ai}{vibe}"

    def as_dict_for_display(self) -> dict[str, Any]:
        """Compact dict suitable for terminal display or skill output."""
        return {
            "primary_language": self.primary_language,
            "languages": self.languages,
            "runners": [r.name for r in self.runners_detected],
            "frameworks": [f.name for f in self.frameworks_detected],
            "infrastructure": [i.kind.value for i in self.infrastructure_detected],
            "plan_files": [str(p.path) for p in self.plan_files_detected],
            "ai_surface": self.ai_surface.value,
            "ai_confidence": self.ai_confidence.value,
            "likely_vibe_coded": self.likely_vibe_coded,
            "total_files_walked": self.total_files_walked,
            "scan_duration_ms": self.scan_duration_ms,
            "scan_status": self.scan_status.value,
        }
