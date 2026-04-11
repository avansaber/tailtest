# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.core.scan — project scanner + `ProjectProfile` schema.

Phase 1 Task 1.12a. The scanner walks a project directory and produces a
`ProjectProfile` describing what tailtest sees: primary language, frameworks,
infrastructure, test runners, plan files, AI-surface markers, and a
`likely_vibe_coded` heuristic.

Two modes per ADR 0010:

- **Shallow** (default, ≤5s, no LLM calls) — file tree walk + manifest parsing
  + deterministic signature matching. What Phase 1 ships.
- **Deep** (opt-in, Phase 3) — everything from shallow, plus one ``claude -p``
  call that produces an `llm_summary`. Not yet implemented; Phase 3 adds it.

The scanner's output is the canonical source of truth for "what is this
project?" — every downstream feature (SessionStart hook, recommendations,
AI-agent detection, dashboard, validator subagent) reads from `ProjectProfile`
instead of doing its own detection.
"""

from tailtest.core.scan.profile import (
    SCAN_SCHEMA_VERSION,
    AIConfidence,
    AISurface,
    DetectedFramework,
    DetectedInfrastructure,
    DetectedPlanFile,
    DetectedRunner,
    DirectoryClassification,
    InfrastructureKind,
    PlanFileKind,
    ProjectProfile,
    ScanStatus,
)
from tailtest.core.scan.scanner import ProjectScanner

__all__ = [
    "SCAN_SCHEMA_VERSION",
    "AIConfidence",
    "AISurface",
    "DetectedFramework",
    "DetectedInfrastructure",
    "DetectedPlanFile",
    "DetectedRunner",
    "DirectoryClassification",
    "InfrastructureKind",
    "PlanFileKind",
    "ProjectProfile",
    "ProjectScanner",
    "ScanStatus",
]
