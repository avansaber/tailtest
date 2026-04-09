"""tailtest.core.findings — the unified finding schema.

Every tailtest producer (runners, security scanners, validators, red-team)
emits `Finding` objects. Every consumer (terminal reporter, dashboard,
baseline manager, MCP tools) reads `Finding` objects. One shape, one
pipeline.

See ADR 0001 (merge test + security into one brand) and Phase 1 Task 1.1
for the design rationale.
"""

from tailtest.core.findings.schema import (
    SCHEMA_VERSION,
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
    compute_finding_id,
)

__all__ = [
    "SCHEMA_VERSION",
    "Finding",
    "FindingBatch",
    "FindingKind",
    "Severity",
    "compute_finding_id",
]
