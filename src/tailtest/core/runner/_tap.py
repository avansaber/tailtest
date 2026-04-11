# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Shared TAP (Test Anything Protocol) parser for JS runner adapters.

Used by NodeTestRunner (TAP fallback), AvaRunner, and TapeRunner.
Handles TAP version 12/13 as emitted by Node.js, ava, and tape.

Grammar subset we care about:
  ok N - test name
  ok N - test name # SKIP reason
  not ok N - test name
  --- (YAML diagnostic block start, after a not ok line)
  key: value
  ... (YAML block end)
  # diagnostic comment (ignored unless it is a summary)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_OK_RE = re.compile(r"^ok\s+\d+\s*-?\s*(.*)")
_NOT_OK_RE = re.compile(r"^not ok\s+\d+\s*-?\s*(.*)")
_SKIP_DIRECTIVE = re.compile(r"#\s*SKIP\b", re.IGNORECASE)


@dataclass
class TapEntry:
    """A single test result extracted from TAP output."""

    name: str
    passed: bool
    skipped: bool = False
    message: str = ""  # failure message from YAML block, or ""


def parse_tap(text: str) -> list[TapEntry]:
    """Parse TAP output into a list of TapEntry objects.

    Tolerant of incomplete or non-standard TAP (e.g., missing plan line,
    extra diagnostic lines). Returns an entry per ``ok`` / ``not ok`` line.
    """
    entries: list[TapEntry] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        ok_m = _OK_RE.match(line)
        if ok_m:
            name_field = ok_m.group(1).strip()
            # Check for SKIP directive anywhere in the name field.
            skipped = bool(_SKIP_DIRECTIVE.search(name_field))
            # Strip the SKIP annotation from the display name.
            name = _SKIP_DIRECTIVE.sub("", name_field).strip(" #").strip()
            entries.append(TapEntry(name=name or "unnamed", passed=True, skipped=skipped))
            i += 1
            continue

        not_ok_m = _NOT_OK_RE.match(line)
        if not_ok_m:
            name = not_ok_m.group(1).strip()
            message = ""
            # Look ahead for a YAML diagnostic block.
            i += 1
            if i < len(lines) and lines[i].strip() == "---":
                i += 1
                yaml_lines: list[str] = []
                while i < len(lines) and lines[i].strip() not in ("...", "---"):
                    yaml_lines.append(lines[i])
                    i += 1
                if i < len(lines):
                    i += 1  # skip closing ... or ---
                message = _extract_message(yaml_lines)
            entries.append(TapEntry(name=name or "unnamed", passed=False, message=message))
            continue

        i += 1

    return entries


def _extract_message(yaml_lines: list[str]) -> str:
    """Pull the most human-readable field out of a TAP YAML diagnostic block.

    Prefers ``message`` > ``error`` > ``actual``/``expected`` summary.
    Falls back to the raw block joined on spaces.
    """
    lines = [ln.strip() for ln in yaml_lines if ln.strip()]
    for prefix in ("message:", "error:", "name:"):
        for ln in lines:
            if ln.lower().startswith(prefix):
                value = ln[len(prefix) :].strip().strip("'\"")
                if value:
                    return value[:200]

    # Fallback: concatenate all lines.
    combined = " ".join(lines)
    return combined[:200] if combined else ""
