# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Unified `Finding` schema — the load-bearing primitive of the validator pipeline.

Every tailtest finding — test failure, lint warning, secret leak, SAST hit, SCA
advisory, coverage gap, validator reasoning, red-team attack result — flows
through this schema. Reporters, the dashboard, the baseline manager, and the
MCP tools all speak `Finding`.

See Phase 1 Task 1.1 for the task spec and ADR 0001 for the merger rationale.

## Finding ID stability rule

Each finding has a 16-character stable ID computed from the tuple
`(kind, file, line, rule_id, normalized_message)`. The message is normalized
to strip line numbers, absolute file paths, ISO timestamps, floats, and hex
addresses so that cosmetic variations don't break the baseline. This means a
test that fails today and fails again tomorrow with a slightly different
error message will produce the same ID, so the baseline can suppress it.

## Schema versioning

Every `Finding` and `FindingBatch` carries a `schema_version` field. Bump it
on any incompatible change. The reader uses the field to decide whether to
accept the data or migrate it.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Bump on any incompatible schema change.
SCHEMA_VERSION = 1


class FindingKind(StrEnum):
    """The category of a finding.

    Extended over Phase 1+2+3+5+6:
    - Phase 1 produces: `test_failure`, `lint`, `coverage_gap`, `ai_surface`
    - Phase 2 produces: `secret`, `sast`, `sca`
    - Phase 5 produces: `validator`
    - Phase 6 produces: `redteam`
    """

    TEST_FAILURE = "test_failure"
    LINT = "lint"
    SECRET = "secret"
    SAST = "sast"
    SCA = "sca"
    COVERAGE_GAP = "coverage_gap"
    AI_SURFACE = "ai_surface"
    VALIDATOR = "validator"
    REDTEAM = "redteam"


class Severity(StrEnum):
    """Severity ranking, ordered from info to critical."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        """Numeric rank for sorting and threshold checks."""
        return _SEVERITY_RANK[self]


_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


# Patterns that should be normalized out of messages for stable ID hashing.
# Each pattern matches "cosmetic" content that can change across runs without
# the underlying finding being different: timestamps, floats (often durations),
# hex addresses, absolute file paths, and line numbers inside other messages.
_NORMALIZE_PATTERNS = [
    # ISO 8601 timestamps (with or without tz)
    re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    # Unix timestamps with fractional seconds (e.g. "1.234s", "0.42ms")
    re.compile(r"\b\d+\.\d+\s*(?:ms|s|us|µs)?\b"),
    # Hex addresses
    re.compile(r"\b0x[0-9a-fA-F]+\b"),
    # Absolute file paths (Unix-style). Windows paths are handled by the
    # file-field exclusion below, not here.
    re.compile(r"(?<![a-zA-Z])/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+"),
    # "at line NNN" / "on line NNN" — test frameworks often embed this
    re.compile(r"(?:at|on|in)\s+line\s+\d+", re.IGNORECASE),
]


def _normalize_message(message: str) -> str:
    """Strip cosmetic variation from a message for stable hashing.

    Removes timestamps, floats, hex addresses, absolute paths, and "at line N"
    fragments. The result is lowercase and whitespace-collapsed.
    """
    text = message
    for pattern in _NORMALIZE_PATTERNS:
        text = pattern.sub("?", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def compute_finding_id(
    kind: FindingKind | str,
    file: Path | str,
    line: int,
    rule_id: str | None,
    message: str,
) -> str:
    """Compute a stable 16-character finding ID.

    Uses `sha256(kind|file|line|rule_id|normalized_message)` truncated to 16
    hex characters. Normalization (see `_normalize_message`) ensures that
    cosmetic differences in the message don't produce a new ID.
    """
    kind_str = kind.value if isinstance(kind, FindingKind) else str(kind)
    file_str = str(file)
    rule = rule_id or ""
    normalized = _normalize_message(message)
    basis = f"{kind_str}|{file_str}|{line}|{rule}|{normalized}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


class Finding(BaseModel):
    """One finding — test failure, security issue, coverage gap, or validator note.

    The `id` field is stable across runs: cosmetic message changes won't
    invalidate the baseline. Construct via `Finding.create()` to compute the
    ID automatically, or pass an explicit `id` if you already have it.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)

    # Schema versioning
    schema_version: int = Field(default=SCHEMA_VERSION, description="Bump on incompatible changes.")

    # Identity
    id: str = Field(description="16-char stable hash. See compute_finding_id().")
    kind: FindingKind
    severity: Severity

    # Location
    file: Path
    line: int
    col: int | None = None

    # Content
    message: str = Field(description="Human-readable message.")
    claude_hint: str | None = Field(
        default=None,
        description="One-sentence hint written for Claude's next turn. "
        "If None, reporters fall back to the message.",
    )
    fix_suggestion: str | None = None
    doc_link: str | None = None
    rule_id: str | None = None

    # Security-specific (Phase 2+)
    cwe_id: str | None = None
    cvss_score: float | None = None
    epss_score: float | None = None
    kev_listed: bool | None = None
    package_name: str | None = None
    package_version: str | None = None
    fixed_version: str | None = None
    advisory_url: str | None = None

    # Validator-specific (Phase 5+)
    reasoning: str | None = None
    confidence: str | None = None  # "high" | "medium" | "low"

    # Lifecycle
    touched_by_edit: bool = False
    in_baseline: bool = False
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def create(
        cls,
        *,
        kind: FindingKind,
        severity: Severity,
        file: Path | str,
        line: int,
        message: str,
        run_id: str,
        rule_id: str | None = None,
        **kwargs: Any,
    ) -> Finding:
        """Convenience constructor that computes the stable ID automatically.

        Use this whenever you have the content of a finding but haven't
        computed the ID yet. Ensures every finding in the pipeline has a
        consistently-computed ID.
        """
        finding_id = compute_finding_id(kind, file, line, rule_id, message)
        return cls(
            id=finding_id,
            kind=kind,
            severity=severity,
            file=Path(file),
            line=line,
            message=message,
            run_id=run_id,
            rule_id=rule_id,
            **kwargs,
        )


class FindingBatch(BaseModel):
    """A collection of findings from a single tailtest run.

    Batches are what PostToolUse hooks, MCP tools, and reporters pass around.
    The `summary_line` is the one-line status a user sees in the terminal;
    the `findings` list is the full detail written to reports.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    run_id: str
    depth: str  # off | quick | standard | thorough | paranoid
    findings: list[Finding] = Field(default_factory=list)
    duration_ms: float = 0.0
    summary_line: str = ""
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    # Delta coverage fields, Phase 1 Task 1.8a. Populated when the
    # runner was invoked with coverage collection AND a diff of
    # added lines. `None` means "not computed this run", distinct
    # from 0.0 which means "new code present but nothing covered".
    delta_coverage_pct: float | None = None
    uncovered_new_lines: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Counts of findings by severity label."""
        result: dict[str, int] = {}
        for finding in self.findings:
            result[finding.severity.value] = result.get(finding.severity.value, 0) + 1
        return result

    @property
    def new_findings(self) -> list[Finding]:
        """Findings NOT in the baseline."""
        return [f for f in self.findings if not f.in_baseline]

    @property
    def has_new_failures(self, threshold: Severity = Severity.MEDIUM) -> bool:
        """True if any non-baseline finding is at or above ``threshold`` severity."""
        return any(f.severity.rank >= threshold.rank for f in self.new_findings)

    def with_baseline_applied(self, baseline_ids: set[str]) -> FindingBatch:
        """Return a new batch with findings in the baseline marked ``in_baseline=True``."""
        new_findings: list[Finding] = []
        for f in self.findings:
            if f.id in baseline_ids:
                new_findings.append(f.model_copy(update={"in_baseline": True}))
            else:
                new_findings.append(f)
        return self.model_copy(update={"findings": new_findings})
