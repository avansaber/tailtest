"""``invoke_validator`` MCP tool -- spawns the Jiminy Cricket validator subagent.

Phase 5 Task 5.2.

At ``thorough`` and ``paranoid`` depths, the PostToolUse hook calls this tool
after the cheap path (tests + lint + security) completes. The tool:

1. Loads ``agents/validator.md`` (system prompt + frontmatter).
2. Builds an initial prompt from the diff, changed file paths, session context,
   and a snippet of ``.tailtest/memory/validator.md``.
3. Spawns ``claude -p`` with restricted tools (Read/Grep/Glob/Bash only).
4. Parses the JSON finding array from stdout.
5. Appends any self-notes the validator appended after the memory marker.
6. Returns a ``FindingBatch`` with ``kind=validator`` findings.

Error contract:
- Subagent timeout → empty FindingBatch + logged warning.
- Subagent crash / invalid JSON → empty FindingBatch + logged error.
- ``claude`` binary not found → error response (caller should disable validator).

Note: subagent invocation via ``claude -p`` is experimental in Phase 5.
      Task 5.8 (dogfood) is the gate for confirming it works end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.mcp.tools.base import BaseTool, ToolResponse, error_response, text_response

logger = logging.getLogger(__name__)

# Maximum characters for the diff in the initial prompt.
# ~20,000 tokens ~ 80,000 chars, but we're cautious.
_MAX_DIFF_CHARS = 8_000
# Maximum characters for the memory snippet.
_MAX_MEMORY_CHARS = 2_000
# Marker the validator writes to separate findings JSON from memory notes.
_MEMORY_MARKER = "<!-- validator-memory-append -->"
# Defensive: reject validator output that looks like a code modification attempt.
_MODIFICATION_PATTERNS = [
    re.compile(r"^[+-]{3}\s", re.MULTILINE),  # unified diff header
    re.compile(r"<write_file>", re.IGNORECASE),
    re.compile(r"<edit_file>", re.IGNORECASE),
]


class InvokeValidatorTool(BaseTool):
    name: ClassVar[str] = "invoke_validator"
    description: ClassVar[str] = (
        "Invoke the Jiminy Cricket validator subagent to reason about whether "
        "a code change preserves correctness and intent. Only fires at "
        "'thorough' and 'paranoid' depths. Returns a FindingBatch with "
        "kind=validator findings. An empty findings list means the validator "
        "found nothing concerning. Pass the diff and list of changed file "
        "paths; the tool builds the full context prompt internally."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths of files changed in this edit (relative to project root).",
            },
            "diff": {
                "type": "string",
                "description": "Unified diff of the change. Truncated internally if too long.",
                "default": "",
            },
            "context": {
                "type": "string",
                "description": "Optional additional context (recent session events, etc.).",
                "default": "",
            },
            "timeout": {
                "type": "number",
                "description": "Subagent timeout in seconds. Default 120. The claude -p startup takes ~15s plus reasoning time; allow at least 120s for real validation work.",
                "default": 120,
            },
        },
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        file_paths: list[str] = arguments.get("file_paths") or []
        diff: str = str(arguments.get("diff") or "")
        context: str = str(arguments.get("context") or "")
        timeout: float = float(arguments.get("timeout") or 30)

        run_id = str(uuid4())

        # 1. Locate and load the validator system prompt.
        prompt_body, err = _load_validator_prompt(self.project_root)
        if err:
            return error_response(f"invoke_validator: {err}")

        # 2. Load memory snippet.
        memory_path = self.project_root / ".tailtest" / "memory" / "validator.md"
        memory_snippet = _load_memory_snippet(memory_path)

        # 3. Build the initial user prompt.
        initial_prompt = _build_initial_prompt(
            file_paths=file_paths,
            diff=diff,
            context=context,
            memory_snippet=memory_snippet,
        )

        # 4. Spawn the validator subagent.
        try:
            raw_output = await _invoke_claude(
                system_prompt=prompt_body,
                initial_prompt=initial_prompt,
                project_root=self.project_root,
                timeout=timeout,
            )
        except _SubagentNotFound:
            return error_response(
                "invoke_validator: 'claude' binary not found on PATH. "
                "Disable the validator with: /tailtest config validator.enabled false"
            )
        except TimeoutError:
            logger.warning("invoke_validator: subagent timed out after %ss", timeout)
            return text_response(
                _empty_batch(run_id, note=f"validator timed out after {timeout}s").model_dump_json(
                    indent=2
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("invoke_validator: subagent failed: %s", exc)
            return text_response(
                _empty_batch(run_id, note=f"validator subagent error: {exc}").model_dump_json(
                    indent=2
                )
            )

        # 5. Defensive check: reject output that looks like code modification.
        for pat in _MODIFICATION_PATTERNS:
            if pat.search(raw_output):
                logger.error(
                    "invoke_validator: defensive layer blocked suspicious output "
                    "(pattern %r). Returning empty findings.",
                    pat.pattern,
                )
                return text_response(
                    _empty_batch(
                        run_id, note="validator output rejected by defensive parser"
                    ).model_dump_json(indent=2)
                )

        # 6. Parse findings JSON + memory note.
        findings_data, memory_note = _parse_validator_output(raw_output)

        # 7. Append memory note.
        if memory_note:
            _append_memory(memory_path, memory_note)

        # 8. Convert raw dicts to Finding objects.
        findings = _to_findings(findings_data, run_id=run_id)

        total = len(findings)
        failed = sum(1 for f in findings if f.severity in (Severity.CRITICAL, Severity.HIGH))
        summary = f"tailtest: validator found {total} issue(s)"
        if total == 0:
            summary = "tailtest: validator found nothing concerning"

        batch = FindingBatch(
            run_id=run_id,
            depth="thorough",
            findings=findings,
            summary_line=summary,
            tests_failed=failed,
        )
        return text_response(batch.model_dump_json(indent=2))


# --- Helpers -----------------------------------------------------------------


class _SubagentNotFound(RuntimeError):
    pass


def _load_validator_prompt(project_root: Path) -> tuple[str, str]:
    """Return (prompt_body, error_message). error_message is empty on success."""
    # Search for agents/validator.md relative to the project root first,
    # then walk up toward / looking for the repo root.
    candidates = [
        project_root / "agents" / "validator.md",
    ]
    # Also check one level up (useful when the MCP server runs from a
    # subdirectory of the repo).
    candidates.append(project_root.parent / "agents" / "validator.md")

    for candidate in candidates:
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8")
            body = _strip_frontmatter(text)
            if body.strip():
                return body, ""

    return "", "agents/validator.md not found (searched project root and parent)"


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter block from the top of a markdown file."""
    if not text.startswith("---"):
        return text
    # Find the closing ---
    rest = text[3:]
    idx = rest.find("---")
    if idx == -1:
        return text
    return rest[idx + 3 :].strip()


def _load_memory_snippet(memory_path: Path) -> str:
    """Return the most recent portion of the validator memory file."""
    if not memory_path.exists():
        return ""
    try:
        content = memory_path.read_text(encoding="utf-8", errors="replace")
        # Return the tail (most recent entries) up to the limit.
        return content[-_MAX_MEMORY_CHARS:] if len(content) > _MAX_MEMORY_CHARS else content
    except OSError:
        return ""


def _build_initial_prompt(
    *,
    file_paths: list[str],
    diff: str,
    context: str,
    memory_snippet: str,
) -> str:
    parts: list[str] = []

    if file_paths:
        parts.append("## Changed files\n" + "\n".join(f"- {p}" for p in file_paths))

    if diff:
        truncated = diff[:_MAX_DIFF_CHARS]
        if len(diff) > _MAX_DIFF_CHARS:
            truncated += f"\n... (diff truncated at {_MAX_DIFF_CHARS} chars)"
        parts.append("## Diff\n```diff\n" + truncated + "\n```")

    if context:
        parts.append("## Session context\n" + context)

    if memory_snippet:
        parts.append("## Validator memory (recent entries)\n" + memory_snippet)

    parts.append(
        "## Your task\n"
        "Follow your process (steps 1-5 from your instructions). "
        "Return your verdict as a JSON array. "
        "An empty array `[]` is a valid and expected response if you find nothing."
    )

    return "\n\n".join(parts)


async def _invoke_claude(
    *,
    system_prompt: str,
    initial_prompt: str,
    project_root: Path,
    timeout: float,
) -> str:
    """Spawn ``claude -p`` and return its stdout."""
    claude_bin = shutil.which("claude")
    if claude_bin is None:
        raise _SubagentNotFound("claude binary not found")

    cmd = [
        claude_bin,
        "-p", initial_prompt,
        "--system-prompt", system_prompt,
        "--allowedTools", "Read,Grep,Glob,Bash",
        "--disallowedTools", "Write,Edit,MultiEdit,NotebookEdit,WebSearch,WebFetch",
        "--no-session-persistence",
        "--output-format", "text",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(project_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        raise TimeoutError() from None

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        logger.warning(
            "invoke_validator: claude exited %d; stderr: %s",
            proc.returncode,
            stderr[:500],
        )
    return stdout


def _parse_validator_output(raw: str) -> tuple[list[dict[str, Any]], str]:
    """Split raw Claude output into (findings_list, memory_note).

    The validator is instructed to output a JSON array then optionally
    a ``<!-- validator-memory-append -->`` marker followed by a note.
    """
    memory_note = ""
    if _MEMORY_MARKER in raw:
        idx = raw.index(_MEMORY_MARKER)
        memory_note = raw[idx + len(_MEMORY_MARKER) :].strip()
        raw = raw[:idx]

    # Extract the JSON array from the remaining text. It may be surrounded
    # by markdown fences or prose.
    json_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not json_match:
        logger.debug("invoke_validator: no JSON array found in output; treating as empty")
        return [], memory_note

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("invoke_validator: JSON parse error: %s", exc)
        return [], memory_note

    if not isinstance(data, list):
        logger.warning("invoke_validator: JSON root is not a list")
        return [], memory_note

    return data, memory_note


def _to_findings(data: list[dict[str, Any]], *, run_id: str) -> list[Finding]:
    """Convert raw validator JSON dicts to Finding objects."""
    results: list[Finding] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_sev = str(item.get("severity") or "medium").lower()
        sev_map = {
            "critical": Severity.CRITICAL,
            "high": Severity.HIGH,
            "medium": Severity.MEDIUM,
            "low": Severity.LOW,
            "info": Severity.INFO,
        }
        severity = sev_map.get(raw_sev, Severity.MEDIUM)
        file_str = str(item.get("file") or "<validator>")
        line = int(item.get("line") or 0)
        message = str(item.get("message") or "validator finding")
        fix_suggestion = item.get("fix_suggestion")
        reasoning = str(item.get("reasoning") or "") or None
        confidence = str(item.get("confidence") or "") or None

        finding = Finding.create(
            kind=FindingKind.VALIDATOR,
            severity=severity,
            file=Path(file_str),
            line=line,
            message=message[:500],
            run_id=run_id,
            rule_id=f"validator::{file_str}:{line}",
            claude_hint=str(fix_suggestion)[:200] if fix_suggestion else None,
        )
        # Attach reasoning and confidence via model_copy so they round-trip
        # through the schema without changing the constructor.
        finding = finding.model_copy(update={"reasoning": reasoning, "confidence": confidence})
        results.append(finding)
    return results


def _append_memory(memory_path: Path, note: str) -> None:
    """Append a dated note to the validator memory file."""
    try:
        memory_path.parent.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        entry = f"\n---\n**{date_str}** {note.strip()}\n"
        with memory_path.open("a", encoding="utf-8") as fh:
            fh.write(entry)
        # Size cap: 10,000 tokens ~ 40,000 chars. Archive if exceeded.
        _maybe_archive_memory(memory_path)
    except OSError as exc:
        logger.warning("invoke_validator: could not append to memory: %s", exc)


def _maybe_archive_memory(memory_path: Path) -> None:
    """Archive the oldest half of the memory file when it exceeds 40,000 chars."""
    try:
        content = memory_path.read_text(encoding="utf-8")
    except OSError:
        return
    if len(content) <= 40_000:
        return
    mid = len(content) // 2
    # Find the nearest entry boundary after the midpoint.
    boundary = content.find("\n---\n", mid)
    if boundary == -1:
        boundary = mid

    archive_content = content[:boundary]
    keep_content = content[boundary:]

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    archive_path = memory_path.parent / f"validator-archive-{ts}.md"
    try:
        archive_path.write_text(archive_content, encoding="utf-8")
        memory_path.write_text(keep_content, encoding="utf-8")
    except OSError as exc:
        logger.warning("invoke_validator: memory archive failed: %s", exc)


def _empty_batch(run_id: str, *, note: str = "") -> FindingBatch:
    summary = "tailtest: validator returned no findings"
    if note:
        summary += f" ({note})"
    return FindingBatch(
        run_id=run_id,
        depth="thorough",
        summary_line=summary,
    )
