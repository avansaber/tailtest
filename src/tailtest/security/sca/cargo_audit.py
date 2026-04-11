# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""cargo audit integration for Rust security scanning (Phase 4.5 Task 4.5.4).

``cargo audit`` scans a ``Cargo.lock`` for known vulnerabilities using the
RustSec advisory database.  It requires a separate ``cargo-audit`` binary:

    cargo install cargo-audit

This module provides:

- ``cargo_audit_available()``: Returns True if ``cargo-audit`` is on PATH.
- ``cargo_audit_scan(crate_root, run_id)``: Runs ``cargo audit --json``
  and converts the output to ``Finding`` objects.

When ``cargo audit`` is available it is preferred over OSV for Rust
because RustSec's advisory database is more complete for the Rust ecosystem
and provides richer fix-suggestion data.  When it is not installed the
caller falls back to ``OSVLookup`` with ``ecosystem="crates.io"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from tailtest.core.findings.schema import Finding, FindingKind, Severity

logger = logging.getLogger(__name__)

# cargo audit exits non-zero when vulnerabilities are found; that is
# not an error from our perspective.
_OK_EXIT_CODES = frozenset({0, 1})


def cargo_audit_available() -> bool:
    """Return True if ``cargo-audit`` is installed and on PATH."""
    return shutil.which("cargo-audit") is not None


async def cargo_audit_scan(
    crate_root: Path,
    run_id: str,
    *,
    timeout_seconds: float = 60.0,
) -> list[Finding]:
    """Run ``cargo audit --json`` in ``crate_root`` and return Findings.

    Returns an empty list when:
    - ``cargo-audit`` is not installed (graceful fallback to OSV).
    - The ``Cargo.lock`` does not exist in ``crate_root``.
    - Any subprocess or parse error occurs (always logs a warning).

    The caller is responsible for merging these findings with OSV findings
    or choosing one over the other.
    """
    if not cargo_audit_available():
        return []

    cargo_lock = crate_root / "Cargo.lock"
    if not cargo_lock.exists():
        logger.debug("cargo audit skipped: no Cargo.lock in %s", crate_root)
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            "cargo-audit",
            "--json",
            cwd=str(crate_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_seconds
        )
    except (OSError, TimeoutError) as exc:
        logger.warning("cargo audit failed: %s", exc)
        return []

    if (proc.returncode or 0) not in _OK_EXIT_CODES:
        stderr = stderr_bytes.decode(errors="replace").strip()
        logger.warning("cargo audit exited %s: %s", proc.returncode, stderr[:200])
        return []

    stdout_text = stdout_bytes.decode(errors="replace")
    return _parse_cargo_audit_json(stdout_text, run_id=run_id)


def _parse_cargo_audit_json(json_text: str, *, run_id: str) -> list[Finding]:
    """Parse ``cargo audit --json`` output into Finding objects.

    The JSON schema emitted by cargo audit (as of 0.20.x):

    .. code-block:: json

        {
          "vulnerabilities": {
            "found": true,
            "count": 2,
            "list": [
              {
                "advisory": {
                  "id": "RUSTSEC-2023-0001",
                  "package": "foo",
                  "title": "...",
                  "description": "...",
                  "severity": "high",
                  "url": "https://rustsec.org/advisories/RUSTSEC-2023-0001.html"
                },
                "versions": {
                  "patched": [">=1.2.3"],
                  "unaffected": []
                },
                "package": {
                  "name": "foo",
                  "version": "1.0.0"
                }
              }
            ]
          }
        }

    Returns an empty list if the JSON cannot be parsed.
    """
    if not json_text.strip():
        return []

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("cargo audit JSON parse error: %s", exc)
        return []

    vuln_section = data.get("vulnerabilities") if isinstance(data, dict) else None
    if not isinstance(vuln_section, dict):
        return []

    vuln_list = vuln_section.get("list")
    if not isinstance(vuln_list, list):
        return []

    findings: list[Finding] = []
    for entry in vuln_list:
        if not isinstance(entry, dict):
            continue

        advisory = entry.get("advisory") or {}
        package_info = entry.get("package") or {}
        versions = entry.get("versions") or {}

        advisory_id = advisory.get("id", "RUSTSEC-UNKNOWN")
        pkg_name = package_info.get("name") or advisory.get("package", "unknown")
        pkg_version = package_info.get("version", "")
        title = advisory.get("title", advisory_id)
        description = advisory.get("description", "")
        severity_str = (advisory.get("severity") or "").lower()
        patched = versions.get("patched") or []
        advisory_url = advisory.get("url", "")

        severity = _map_rustsec_severity(severity_str)

        fix_parts: list[str] = []
        if patched:
            fix_parts.append(f"Patched in: {', '.join(str(v) for v in patched[:3])}")
        if advisory_url:
            fix_parts.append(advisory_url)
        fix_hint = " | ".join(fix_parts) or None

        message = f"[{advisory_id}] {pkg_name} {pkg_version}: {title}"
        if description:
            # Trim long descriptions to keep context concise.
            short_desc = description.strip().replace("\n", " ")[:200]
            message = f"{message}. {short_desc}"

        findings.append(
            Finding.create(
                kind=FindingKind.SCA,
                severity=severity,
                file=Path("Cargo.lock"),
                line=0,
                message=message,
                run_id=run_id,
                rule_id=f"rustsec::{advisory_id}",
                fix_suggestion=fix_hint,
            )
        )

    return findings


def _map_rustsec_severity(severity_str: str) -> Severity:
    """Map a RustSec severity string to a unified Severity enum value."""
    mapping = {
        "critical": Severity.CRITICAL,
        "high": Severity.HIGH,
        "medium": Severity.MEDIUM,
        "low": Severity.LOW,
    }
    return mapping.get(severity_str, Severity.MEDIUM)
