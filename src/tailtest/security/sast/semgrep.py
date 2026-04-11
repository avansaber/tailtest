# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""SemgrepRunner, SAST scanning for tailtest (Phase 2 Task 2.2).

Shells out to ``semgrep --config <ruleset> --json --quiet <files>``
and parses the JSON output into ``Finding`` objects with
``kind=sast``. Mirrors the ``GitleaksRunner`` shape so the two
security wrappers stay symmetric and easy to maintain in parallel.

Design notes:

- **Batch scanning.** Unlike gitleaks, Semgrep is optimized for
  scanning multiple files in one invocation. We pass all the
  changed files on a single command line and parse the aggregated
  JSON result, rather than spawning per-file subprocesses. Keeps
  the hot loop fast even when Claude edits several files at once.
- **Default ruleset is ``p/default``**, which Semgrep describes as
  "a curated set of rules from all languages with low false
  positives and high signal." Users can override via the
  ``ruleset`` parameter or eventually via
  ``.tailtest/config.yaml`` ``security.sast.ruleset``.
- **JSON on stdout, not a temp file.** Semgrep writes its JSON
  report to stdout when given ``--json``, which is simpler than
  gitleaks' tempfile dance.
- **Severity mapping**: Semgrep emits one of ``ERROR``, ``WARNING``,
  ``INFO`` per finding. We map them to our unified severity enum:
  ``ERROR``  -> ``Severity.HIGH`` (the default for actionable SAST hits)
  ``WARNING`` -> ``Severity.MEDIUM``
  ``INFO``    -> ``Severity.LOW``
  Unknown or missing severity falls back to ``Severity.MEDIUM`` so
  the finding still surfaces rather than getting silently dropped.
- **Semgrep crashes cleanly by exit code.** A nonzero exit means
  Semgrep itself errored (missing ruleset, parse failure on
  source, etc.). We log at warning level and return an empty list
  rather than propagating the failure up the hot loop. The
  earlier hot-loop design principle holds: security scan failures
  NEVER break the runner.
- **Graceful fallback when Semgrep is missing** via
  ``is_available()``. Same pattern as GitleaksRunner.
- **Ruleset coverage validation (audit gap #10)** is the job of
  Task 2.10's dogfood step, not this class. Task 2.2 ships the
  wrapper; Task 2.10 verifies the default ruleset catches the
  canonical fixtures per supported language.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tailtest.core.findings.schema import Finding, FindingKind, Severity

logger = logging.getLogger(__name__)

# Default Semgrep ruleset: curated, low false-positive, covers the
# top OWASP / CWE patterns across supported languages.
DEFAULT_RULESET = "p/default"

# Maximum characters in a single finding message after trimming.
# Same budget as the gitleaks wrapper and the pytest runner.
_MESSAGE_MAX_CHARS = 200


class SemgrepNotAvailable(RuntimeError):
    """Raised internally when the semgrep binary cannot be located."""


@dataclass(frozen=True)
class _SemgrepHit:
    """Intermediate representation of one Semgrep JSON result.

    Kept separate from ``Finding`` so the JSON-parsing layer stays
    pure and the ``Finding`` construction can be tested with canned
    inputs. Same pattern as ``_GitleaksHit`` in the gitleaks wrapper.
    """

    check_id: str
    message: str
    severity: str  # ERROR | WARNING | INFO (semgrep-native)
    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    cwe: list[str]
    owasp: list[str]
    references: list[str]


class SemgrepRunner:
    """Wraps ``semgrep --json --quiet`` for batch SAST scanning.

    Parameters
    ----------
    project_root:
        Project root used as the working directory for the semgrep
        subprocess call and for computing relative file paths in
        the returned findings.
    semgrep_bin:
        Binary name or absolute path. Defaults to ``"semgrep"``.
    ruleset:
        Semgrep ruleset identifier. Defaults to ``"p/default"``.
        Overridable per-instance for callers that want a stricter
        or project-specific ruleset.
    timeout_seconds:
        Overall scan timeout. 60 seconds is generous; semgrep
        typically scans a batch of changed files in a few seconds.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        semgrep_bin: str = "semgrep",
        ruleset: str = DEFAULT_RULESET,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.semgrep_bin = semgrep_bin
        self.ruleset = ruleset
        self.timeout_seconds = timeout_seconds

    # --- Availability -----------------------------------------------

    def is_available(self) -> bool:
        """Return True when the semgrep binary resolves on PATH."""
        return shutil.which(self.semgrep_bin) is not None

    # --- Public scan API --------------------------------------------

    async def scan(self, files: list[Path], *, run_id: str) -> list[Finding]:
        """Scan the given files for SAST hits, return Findings.

        Returns an empty list when semgrep is not available, when
        ``files`` is empty, or when no hits were found. Any
        subprocess or parse error is logged and the batch is
        treated as "no findings" so the hot loop stays alive.
        """
        if not self.is_available():
            logger.info(
                "semgrep not on PATH, skipping SAST scan "
                "(install via `brew install semgrep` or equivalent)"
            )
            return []

        if not files:
            return []

        # Semgrep accepts a list of files or directories as
        # positional arguments. Pass every file in one invocation
        # rather than spawning per-file subprocesses.
        file_args = [str(f.resolve() if not f.is_absolute() else f) for f in files]

        cmd = [
            self.semgrep_bin,
            "--config",
            self.ruleset,
            "--json",
            "--quiet",
            "--no-git-ignore",
            *file_args,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.project_root,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except FileNotFoundError as exc:
            raise SemgrepNotAvailable(f"semgrep binary not found: {exc}") from exc
        except TimeoutError:
            logger.info("semgrep scan timed out after %ss", self.timeout_seconds)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("semgrep subprocess failed: %s", exc)
            return []

        # Semgrep exits nonzero on internal errors but ALSO on
        # "findings present" in some configurations. We trust the
        # JSON on stdout regardless of the exit code, and only
        # warn when stdout is empty AND exit code was nonzero.
        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        if not stdout_text.strip():
            if (proc.returncode or 0) != 0:
                logger.warning(
                    "semgrep produced no output, exit code %s, stderr: %s",
                    proc.returncode,
                    stderr_text[:500].strip() or "(empty)",
                )
            return []

        hits = parse_semgrep_json(stdout_text)
        return [self._hit_to_finding(hit, run_id=run_id) for hit in hits]

    # --- Hit conversion ----------------------------------------------

    def _hit_to_finding(self, hit: _SemgrepHit, *, run_id: str) -> Finding:
        """Turn a ``_SemgrepHit`` into a unified ``Finding`` object."""
        file_path = Path(hit.path) if hit.path else Path("<unknown>")
        severity = _semgrep_severity_to_unified(hit.severity)
        message = _summarize(f"{hit.message} (rule: {hit.check_id})")

        # Prefer the first reference URL as the doc link. Callers
        # can open it for the full rule explanation.
        doc_link = hit.references[0] if hit.references else None

        claude_hint = _build_claude_hint(hit)

        # Extract the first CWE-NNN identifier from the metadata.
        # Semgrep emits CWE entries like
        # "CWE-94: Improper Control of Generation of Code ('Code
        # Injection')". We only keep the canonical CWE-NNN prefix so
        # reporters and downstream queries can match on a stable key.
        cwe_id = _first_cwe_id(hit.cwe)

        return Finding.create(
            kind=FindingKind.SAST,
            severity=severity,
            file=file_path,
            line=hit.start_line,
            col=hit.start_col,
            message=message,
            run_id=run_id,
            rule_id=f"semgrep::{hit.check_id}",
            doc_link=doc_link,
            advisory_url=doc_link,
            cwe_id=cwe_id,
            claude_hint=claude_hint,
        )


# --- Pure JSON parser (testable without subprocess) --------------------


def parse_semgrep_json(json_text: str) -> list[_SemgrepHit]:
    """Parse Semgrep's JSON report into a list of ``_SemgrepHit``.

    Semgrep's shape:

        {
          "results": [
            {
              "check_id": "...",
              "path": "...",
              "start": {"line": N, "col": N},
              "end": {"line": N, "col": N},
              "extra": {
                "message": "...",
                "severity": "ERROR",
                "metadata": {
                  "cwe": [...],
                  "owasp": [...],
                  "references": [...]
                }
              }
            }
          ],
          "errors": []
        }

    Returns an empty list on any parse failure so the caller can
    treat "no findings" and "broken report" identically. Logs a
    warning on unexpected shapes; does not raise.
    """
    if not json_text or not json_text.strip():
        return []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("semgrep JSON parse failed: %s", exc)
        return []

    if not isinstance(data, dict):
        logger.warning("semgrep JSON root was not an object: %s", type(data).__name__)
        return []

    results = data.get("results")
    if not isinstance(results, list):
        return []

    hits: list[_SemgrepHit] = []
    for raw in results:
        if not isinstance(raw, dict):
            continue
        hit = _raw_to_hit(raw)
        if hit is not None:
            hits.append(hit)
    return hits


def _raw_to_hit(raw: dict[str, Any]) -> _SemgrepHit | None:
    """Build a ``_SemgrepHit`` from one raw semgrep result."""
    try:
        check_id = str(raw.get("check_id") or "")
        path = str(raw.get("path") or "")

        # Narrow each nested dict via an intermediate variable so
        # pyright can follow the isinstance check. Inlining the
        # get() call twice produces two independent reads that the
        # type checker cannot relate.
        start_raw = raw.get("start")
        start: dict[str, Any] = start_raw if isinstance(start_raw, dict) else {}
        end_raw = raw.get("end")
        end: dict[str, Any] = end_raw if isinstance(end_raw, dict) else {}

        start_line = _coerce_int(start.get("line"), default=0)
        start_col = _coerce_int(start.get("col"), default=0)
        end_line = _coerce_int(end.get("line"), default=0)
        end_col = _coerce_int(end.get("col"), default=0)

        extra_raw = raw.get("extra")
        extra: dict[str, Any] = extra_raw if isinstance(extra_raw, dict) else {}
        message = str(extra.get("message") or "")
        severity = str(extra.get("severity") or "").upper()

        metadata_raw = extra.get("metadata")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        cwe = _coerce_string_list(metadata.get("cwe"))
        owasp = _coerce_string_list(metadata.get("owasp"))
        references = _coerce_string_list(metadata.get("references"))

        return _SemgrepHit(
            check_id=check_id,
            message=message,
            severity=severity,
            path=path,
            start_line=start_line,
            start_col=start_col,
            end_line=end_line,
            end_col=end_col,
            cwe=cwe,
            owasp=owasp,
            references=references,
        )
    except (TypeError, ValueError) as exc:
        logger.warning("failed to parse semgrep result %s: %s", raw, exc)
        return None


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _coerce_string_list(value: Any) -> list[str]:
    """Coerce a metadata field to a list of strings.

    Semgrep sometimes emits metadata fields as strings and sometimes
    as lists. Normalize to list of strings for consumers that want
    uniform access.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return []


def _semgrep_severity_to_unified(severity: str) -> Severity:
    """Map a Semgrep severity string to our unified Severity enum."""
    upper = severity.upper().strip()
    return {
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "INFO": Severity.LOW,
    }.get(upper, Severity.MEDIUM)


def _build_claude_hint(hit: _SemgrepHit) -> str | None:
    """Build a short actionable hint from the Semgrep metadata."""
    parts: list[str] = []
    if hit.owasp:
        parts.append(f"OWASP: {', '.join(hit.owasp[:2])}")
    if hit.cwe:
        parts.append(f"CWE: {', '.join(hit.cwe[:2])}")
    if hit.references:
        parts.append(f"See: {hit.references[0]}")
    if not parts:
        return None
    hint = " | ".join(parts)
    if len(hint) > _MESSAGE_MAX_CHARS:
        return hint[: _MESSAGE_MAX_CHARS - 3] + "..."
    return hint


_CWE_PATTERN = re.compile(r"CWE-\d+", re.IGNORECASE)


def _first_cwe_id(cwe_entries: list[str]) -> str | None:
    """Extract the first canonical ``CWE-NNN`` identifier from Semgrep metadata.

    Semgrep's CWE field is a list of strings like
    ``"CWE-94: Improper Control of Generation of Code ('Code
    Injection')"``. We keep only the leading ``CWE-NNN`` prefix
    so downstream matching and reporting can rely on a stable key.
    Returns None when no entry contains a matchable ``CWE-`` token.
    """
    for entry in cwe_entries:
        if not isinstance(entry, str):
            continue
        match = _CWE_PATTERN.search(entry)
        if match:
            return match.group(0).upper()
    return None


def _summarize(text: str) -> str:
    """Trim a semgrep message to one compact line."""
    stripped = text.strip().replace("\n", " ")
    if len(stripped) <= _MESSAGE_MAX_CHARS:
        return stripped
    return stripped[: _MESSAGE_MAX_CHARS - 3] + "..."
