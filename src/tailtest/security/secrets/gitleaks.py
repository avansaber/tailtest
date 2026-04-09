"""GitleaksRunner, secret scanning for tailtest (Phase 2 Task 2.1).

Shells out to ``gitleaks detect --source <path> --no-git
--report-format json --report-path <tmp>`` and parses the JSON
output into ``Finding`` objects with ``kind=secret``.

Design notes:

- **Per-file scanning.** The hook gets a list of changed files from
  the PostToolUse payload. We scan each file independently so the
  per-file-to-finding mapping is trivial. For large bulk scans a
  future caller can pass a directory and let gitleaks walk it; the
  parser handles both shapes.
- **No-git mode.** The hook runs against files that Claude just
  edited, not against git history. ``--no-git`` tells gitleaks to
  treat the path as a regular file or directory instead of
  assuming a git repo, which is the correct behavior for the hot
  loop (git staging state is irrelevant).
- **JSON report via temp file.** gitleaks writes its JSON report
  to a file, not stdout, when ``--report-path`` is set. We use a
  tempdir per invocation so parallel scans do not stomp on each
  other.
- **Graceful fallback.** When the ``gitleaks`` binary is not on
  PATH, ``scan()`` returns an empty list and logs one INFO-level
  message. The hook caller reads ``is_available()`` first and
  skips the scan cleanly when False, so missing gitleaks never
  breaks a hot loop.
- **Severity mapping.** Every gitleaks hit maps to ``Severity.HIGH``
  by default. Verified-secret escalation to ``CRITICAL`` is a
  Phase 2 Task 2.1 follow-up and ships when we wire up
  secret verification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tailtest.core.findings.schema import Finding, FindingKind, Severity

logger = logging.getLogger(__name__)


class GitleaksNotAvailable(RuntimeError):
    """Raised internally when the gitleaks binary cannot be located.

    Callers should prefer :meth:`GitleaksRunner.is_available` to a
    try/except around this exception, so the absence of gitleaks is
    a cheap boolean check, not a thrown-and-caught error.
    """


@dataclass(frozen=True)
class _GitleaksHit:
    """Intermediate representation of one gitleaks JSON record.

    Kept separate from ``Finding`` so the JSON-parsing layer can
    stay pure and the ``Finding`` construction can be tested with
    canned inputs.
    """

    rule_id: str
    description: str
    file: str
    start_line: int
    start_column: int
    secret: str
    entropy: float
    fingerprint: str


class GitleaksRunner:
    """Wraps ``gitleaks detect`` for per-file secret scanning.

    Parameters
    ----------
    project_root:
        Project root used as the working directory for gitleaks
        subprocess calls and for computing relative file paths in
        the returned findings.
    gitleaks_bin:
        Binary name or absolute path. Defaults to ``"gitleaks"`` so
        a project-local install or a PATH entry both work.
    timeout_seconds:
        Per-file scan timeout. 10 seconds is generous; gitleaks
        scans individual files in milliseconds.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        gitleaks_bin: str = "gitleaks",
        timeout_seconds: float = 10.0,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.gitleaks_bin = gitleaks_bin
        self.timeout_seconds = timeout_seconds

    # --- Availability -----------------------------------------------

    def is_available(self) -> bool:
        """Return True when the gitleaks binary resolves on PATH.

        Cheap and side-effect free. Callers use this to decide
        whether to skip the scan without triggering a subprocess.
        """
        return shutil.which(self.gitleaks_bin) is not None

    # --- Public scan API --------------------------------------------

    async def scan(self, files: list[Path], *, run_id: str) -> list[Finding]:
        """Scan each file in ``files`` for secrets, return Findings.

        Returns an empty list when gitleaks is not available, when
        ``files`` is empty, or when no secrets were found. Any
        per-file subprocess error is logged and the file is
        skipped; other files in the batch continue.
        """
        if not self.is_available():
            logger.info(
                "gitleaks not on PATH, skipping secret scan "
                "(install via `brew install gitleaks` or equivalent)"
            )
            return []

        if not files:
            return []

        findings: list[Finding] = []
        for file_path in files:
            try:
                file_findings = await self._scan_single(file_path, run_id=run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("gitleaks scan failed for %s: %s", file_path, exc)
                continue
            findings.extend(file_findings)
        return findings

    # --- Single-file scan + parse ------------------------------------

    async def _scan_single(self, file_path: Path, *, run_id: str) -> list[Finding]:
        """Scan a single file and return the parsed Findings."""
        absolute = file_path.resolve() if not file_path.is_absolute() else file_path

        with tempfile.TemporaryDirectory(prefix="tailtest-gitleaks-") as tmp_dir:
            report_path = Path(tmp_dir) / "report.json"
            cmd = [
                self.gitleaks_bin,
                "detect",
                "--source",
                str(absolute),
                "--no-git",
                "--report-format",
                "json",
                "--report-path",
                str(report_path),
                "--no-banner",
                "--exit-code",
                "0",  # treat "leaks found" as success, not exit 1
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.project_root,
                )
                await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
            except FileNotFoundError as exc:
                raise GitleaksNotAvailable(f"gitleaks binary not found: {exc}") from exc
            except TimeoutError:
                logger.info("gitleaks scan timed out on %s", absolute)
                return []

            if not report_path.exists():
                # gitleaks skipped writing the report (usually means
                # zero findings on this file or a permission error).
                return []

            hits = parse_gitleaks_json(report_path.read_text(encoding="utf-8"))
            return [self._hit_to_finding(hit, run_id=run_id) for hit in hits]

    # --- Hit conversion ----------------------------------------------

    def _hit_to_finding(self, hit: _GitleaksHit, *, run_id: str) -> Finding:
        """Turn a ``_GitleaksHit`` into a unified ``Finding`` object.

        Kind is always SECRET. Severity is HIGH by default; a
        future revision may escalate to CRITICAL when gitleaks
        marks the finding as "verified" against a live API.
        """
        file_path = Path(hit.file) if hit.file else Path("<unknown>")
        message = f"{hit.description} (rule: {hit.rule_id})"
        return Finding.create(
            kind=FindingKind.SECRET,
            severity=Severity.HIGH,
            file=file_path,
            line=hit.start_line,
            col=hit.start_column,
            message=_summarize(message),
            run_id=run_id,
            rule_id=f"gitleaks::{hit.rule_id}",
            claude_hint=(
                "Remove the secret from the file and rotate it immediately "
                "if it was ever committed, pushed, or shared."
            ),
        )


# --- Pure JSON parser (testable without subprocess) --------------------


def parse_gitleaks_json(json_text: str) -> list[_GitleaksHit]:
    """Parse gitleaks' JSON report text into a list of ``_GitleaksHit``.

    Returns an empty list on any parse failure so callers can treat
    "no findings" and "broken report" identically. Logs a warning
    on unexpected shapes; does not raise.
    """
    if not json_text or not json_text.strip():
        return []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        logger.warning("gitleaks JSON parse failed: %s", exc)
        return []

    if data is None:
        return []
    if not isinstance(data, list):
        logger.warning("gitleaks JSON root was not a list: %s", type(data).__name__)
        return []

    hits: list[_GitleaksHit] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        hit = _raw_to_hit(raw)
        if hit is not None:
            hits.append(hit)
    return hits


def _raw_to_hit(raw: dict[str, Any]) -> _GitleaksHit | None:
    """Build a ``_GitleaksHit`` from one raw JSON dict, or None on failure.

    gitleaks field names are in PascalCase. We handle the ones we
    need and ignore the rest (Author, Email, Date, Message, etc.)
    since the hook path does not surface committer metadata.
    """
    try:
        return _GitleaksHit(
            rule_id=str(raw.get("RuleID") or ""),
            description=str(raw.get("Description") or ""),
            file=str(raw.get("File") or ""),
            start_line=_coerce_int(raw.get("StartLine"), default=0),
            start_column=_coerce_int(raw.get("StartColumn"), default=0),
            secret=str(raw.get("Secret") or ""),
            entropy=_coerce_float(raw.get("Entropy"), default=0.0),
            fingerprint=str(raw.get("Fingerprint") or ""),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("failed to parse gitleaks hit %s: %s", raw, exc)
        return None


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _summarize(text: str, *, max_chars: int = 200) -> str:
    """Trim a gitleaks message to one compact line."""
    stripped = text.strip().replace("\n", " ")
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3] + "..."
