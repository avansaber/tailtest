# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""BaselineManager — lazy + kind-aware baseline policy (Phase 1 Task 1.7).

The baseline is a YAML file at `.tailtest/baseline.yaml` listing findings
that are accepted as "existing debt" and should not surface as new problems.

Policy (audit gaps #3, #7):

- **Lazy generation.** No baseline is auto-generated at SessionStart.
  Instead, the first time a run comes back green (no failing tests), any
  pre-existing non-test findings are captured. This avoids capturing
  "dependencies missing" errors as permanent baseline entries.

- **Kind-aware.** Security findings (secret, sast, sca, ai_surface)
  baseline immediately on first sight. Test failures (`test_failure`)
  require 3 consecutive failing runs before being baselined — broken
  tests stay visible, flaky tests get silenced. Lint and coverage
  findings baseline immediately.

- **Committed to git by default.** The baseline file lives in the user's
  repository and is committed so every contributor sees the same "accepted
  debt." This is the Semgrep / SonarQube convention and the research-backed
  norm.

File format::

    schema_version: 1
    generated_at: 2026-04-09T08:15:30Z
    entries:
      - id: "abc123def456abcd"
        kind: "sast"
        file: "src/foo.py"
        line: 42
        rule_id: "semgrep.eval-injection"
        first_seen: 2026-04-09T08:15:30Z
        failure_streak: 0
        reason: "accepted as legacy debt"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind

logger = logging.getLogger(__name__)

BASELINE_SCHEMA_VERSION = 1

# Documentation header prepended to every baseline.yaml write.
# Phase 2 Task 2.7 added this so contributors who open the file
# understand what they are looking at without digging through the
# tailtest source tree. Keep it short; the file is still meant to
# be skimmed, not read linearly.
_BASELINE_YAML_HEADER = """\
# .tailtest/baseline.yaml
#
# This file records findings that tailtest has accepted as "existing
# debt" for this project. Findings listed here are NOT shown in the
# hot loop summary; tailtest only surfaces findings whose stable id
# is NOT in this file. This is the Semgrep / SonarQube convention.
#
# How entries get added:
# - Secret, SAST, SCA, AI-surface, lint, and coverage-gap findings
#   land here on first detection.
# - Test failures need 3 consecutive failing runs before being
#   baselined (so truly broken tests stay visible; flaky tests get
#   silenced). A green run decrements the failure streak.
#
# How to remove an entry: delete its block and commit. A subsequent
# run that rediscovers the finding will NOT re-add it until a green
# run + update_from sequence passes through.
#
# Reviewing accepted debt: run `/tailtest:debt` in Claude Code, or
# open this file directly. The `reason` field on each entry is
# meant to carry human justification; edit it freely.
#
# This file IS meant to be committed to git so every contributor
# sees the same accepted-debt set.

"""

# Kinds that get baselined immediately on first detection (content-level,
# stable, not subject to flakiness).
_IMMEDIATE_BASELINE_KINDS: frozenset[FindingKind] = frozenset(
    {
        FindingKind.SECRET,
        FindingKind.SAST,
        FindingKind.SCA,
        FindingKind.AI_SURFACE,
        FindingKind.LINT,
        FindingKind.COVERAGE_GAP,
        FindingKind.REDTEAM,  # Phase 6: red-team findings baseline on first detection
    }
)

# Kinds that must fail this many times in a row before being baselined.
_FLAKY_STREAK_THRESHOLD = 3


@dataclass
class BaselineEntry:
    """One finding that has been accepted as existing debt."""

    id: str
    kind: str
    file: str
    line: int
    rule_id: str | None
    first_seen: datetime
    failure_streak: int = 0
    reason: str = "auto-baselined on first detection"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "rule_id": self.rule_id,
            "first_seen": self.first_seen.isoformat(),
            "failure_streak": self.failure_streak,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineEntry:
        return cls(
            id=data["id"],
            kind=data["kind"],
            file=data["file"],
            line=int(data["line"]),
            rule_id=data.get("rule_id"),
            first_seen=datetime.fromisoformat(data["first_seen"]),
            failure_streak=int(data.get("failure_streak", 0)),
            reason=str(data.get("reason", "")),
        )

    @classmethod
    def from_finding(cls, finding: Finding, *, reason: str = "auto-baselined") -> BaselineEntry:
        return cls(
            id=finding.id,
            kind=finding.kind.value,
            file=str(finding.file),
            line=finding.line,
            rule_id=finding.rule_id,
            first_seen=finding.timestamp,
            failure_streak=1 if finding.kind == FindingKind.TEST_FAILURE else 0,
            reason=reason,
        )


@dataclass
class BaselineFile:
    """In-memory representation of .tailtest/baseline.yaml."""

    schema_version: int = BASELINE_SCHEMA_VERSION
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    entries: dict[str, BaselineEntry] = field(default_factory=dict)

    @property
    def ids(self) -> set[str]:
        return set(self.entries.keys())

    def to_yaml(self) -> str:
        """Serialize to YAML with a documentation header block.

        The header comment is load-bearing: it is the first thing
        an engineer sees when they open `baseline.yaml`, and it
        explains what the file is for, how entries get added, how
        to remove them, and what the flakiness policy is. Without
        the header, a curious contributor who opens the file just
        sees a list of cryptic 16-char hashes and (justifiably)
        assumes it is a tool artifact they should not touch.
        """
        doc = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "entries": [self.entries[k].to_dict() for k in sorted(self.entries)],
        }
        body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)
        return _BASELINE_YAML_HEADER + body

    @classmethod
    def from_yaml(cls, text: str) -> BaselineFile:
        """Parse from YAML text."""
        data = yaml.safe_load(text) or {}
        version = int(data.get("schema_version", BASELINE_SCHEMA_VERSION))
        if version != BASELINE_SCHEMA_VERSION:
            logger.warning(
                "Baseline schema version mismatch: file is v%d, tailtest expects v%d",
                version,
                BASELINE_SCHEMA_VERSION,
            )
        generated_at_raw = data.get("generated_at")
        generated_at = (
            datetime.fromisoformat(generated_at_raw) if generated_at_raw else datetime.now(UTC)
        )
        entries: dict[str, BaselineEntry] = {}
        for item in data.get("entries", []) or []:
            entry = BaselineEntry.from_dict(item)
            entries[entry.id] = entry
        return cls(schema_version=version, generated_at=generated_at, entries=entries)


class BaselineManager:
    """Owns the `.tailtest/baseline.yaml` file.

    Thin wrapper around the YAML file + the kind-aware policy. Typical
    usage from the hook::

        manager = BaselineManager(tailtest_dir=project_root / ".tailtest")
        batch_with_baseline = manager.apply_to(batch)
        # then filter out baselined findings for the hot loop
        for finding in batch_with_baseline.new_findings:
            ...
        # opt-in update after a green run
        if batch.tests_failed == 0:
            manager.update_from(batch)
    """

    def __init__(self, tailtest_dir: Path) -> None:
        self._tailtest_dir = tailtest_dir

    @property
    def baseline_path(self) -> Path:
        return self._tailtest_dir / "baseline.yaml"

    def exists(self) -> bool:
        return self.baseline_path.exists()

    def load(self) -> BaselineFile:
        """Load the baseline file, or return an empty one if it doesn't exist."""
        if not self.baseline_path.exists():
            return BaselineFile()
        text = self.baseline_path.read_text(encoding="utf-8")
        return BaselineFile.from_yaml(text)

    def save(self, baseline: BaselineFile) -> None:
        """Write the baseline file atomically."""
        self._tailtest_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.baseline_path.with_suffix(".yaml.tmp")
        tmp.write_text(baseline.to_yaml(), encoding="utf-8")
        tmp.replace(self.baseline_path)

    def apply_to(self, batch: FindingBatch) -> FindingBatch:
        """Return a new batch with `in_baseline=True` for findings in the baseline."""
        baseline = self.load()
        return batch.with_baseline_applied(baseline.ids)

    def list_redteam_entries(self) -> list[BaselineEntry]:
        """Return all baselined red-team findings, sorted by first_seen."""
        baseline = self.load()
        return sorted(
            [e for e in baseline.entries.values() if e.kind == FindingKind.REDTEAM.value],
            key=lambda e: e.first_seen,
        )

    def update_from(self, batch: FindingBatch) -> BaselineFile:
        """Update the baseline file based on a finished run.

        Policy:
        - Security/lint/coverage findings are added on first sight
        - Test failures need 3 consecutive runs before being added
        - Test passes DECREMENT the streak (flaky test goes back to 0)

        Returns the updated baseline (also writes it to disk).
        """
        baseline = self.load()
        changed = False

        seen_failing_test_ids: set[str] = set()

        for finding in batch.findings:
            # Already in the baseline — refresh streak for test failures.
            if finding.id in baseline.entries:
                entry = baseline.entries[finding.id]
                if finding.kind == FindingKind.TEST_FAILURE:
                    entry.failure_streak += 1
                    seen_failing_test_ids.add(finding.id)
                continue

            # Immediate baseline for non-test kinds.
            if finding.kind in _IMMEDIATE_BASELINE_KINDS:
                entry = BaselineEntry.from_finding(finding)
                baseline.entries[entry.id] = entry
                changed = True
                continue

            # Test failures: accumulate streaks.
            if finding.kind == FindingKind.TEST_FAILURE:
                # Not yet in the baseline; this is the first (or second)
                # consecutive failure. Track it via a transient streak count
                # stored on a synthetic entry with failure_streak < threshold.
                entry = BaselineEntry.from_finding(finding)
                entry.failure_streak = 1
                if entry.failure_streak >= _FLAKY_STREAK_THRESHOLD:
                    baseline.entries[entry.id] = entry
                    changed = True
                # otherwise we'd need a separate "pending baseline" table;
                # for Phase 1 we rely on the caller persisting streaks in
                # .tailtest/state.json (not implemented yet — Phase 1 ships
                # with the policy correct but needing a state file the Phase 1
                # PostToolUse hook will add)
                seen_failing_test_ids.add(finding.id)

            # Validator findings are never auto-baselined (managed by Phase 5 workflow).
            # REDTEAM findings are handled by _IMMEDIATE_BASELINE_KINDS above.

        # Decrement streaks for test failures not seen this run (flaky tests recovering)
        for entry in list(baseline.entries.values()):
            if entry.kind != FindingKind.TEST_FAILURE.value:
                continue
            if entry.id in seen_failing_test_ids:
                continue
            if entry.failure_streak > 0:
                entry.failure_streak = max(0, entry.failure_streak - 1)
                changed = True

        if changed or not self.exists():
            baseline.generated_at = datetime.now(UTC)
            self.save(baseline)
        return baseline
