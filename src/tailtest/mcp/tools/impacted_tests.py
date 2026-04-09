"""``impacted_tests`` MCP tool — returns test IDs affected by a set of changed files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

# Import the Python runner module so it self-registers with the default registry.
# Runners are registered at import time via the @register_runner decorator.
import tailtest.core.runner.python  # noqa: F401
from tailtest.core.runner import RunnerNotAvailable, get_default_registry
from tailtest.mcp.tools.base import BaseTool, ToolResponse, error_response, text_response


class ImpactedTestsTool(BaseTool):
    name: ClassVar[str] = "impacted_tests"
    description: ClassVar[str] = (
        "Given a list of changed files (typically the file(s) Claude just edited), "
        "return the test IDs that should be run to verify the change. Uses native "
        "TIA tools when available (pytest-testmon, jest --findRelatedTests) and "
        "falls back to a heuristic scan otherwise. Returns an empty list if no "
        "runner can handle the project."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of paths (relative to the project root) of changed files.",
            },
            "diff": {
                "type": "string",
                "description": "Optional unified diff. Improves TIA accuracy when provided.",
            },
        },
        "required": ["files"],
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        files_raw = arguments.get("files") or []
        if not isinstance(files_raw, list):
            return error_response("`files` must be a list of strings")
        diff = arguments.get("diff")

        try:
            changed_files = [Path(f) for f in files_raw]
        except (TypeError, ValueError) as exc:
            return error_response(f"invalid file path in `files`: {exc}")

        registry = get_default_registry()
        runners = registry.all_for_project(self.project_root)
        if not runners:
            return text_response(
                json.dumps({"test_ids": [], "reason": "no runners detected"}, indent=2)
            )

        # Phase 1: if multiple runners match (monorepo), use the first one.
        # Phase 2+ can partition files by language and dispatch to each runner.
        runner = runners[0]
        try:
            ids = await runner.impacted(changed_files, diff=diff)
        except RunnerNotAvailable as exc:
            return error_response(f"{runner.name}: runner not available: {exc}")
        except Exception as exc:  # noqa: BLE001
            return error_response(f"{runner.name}.impacted() failed: {type(exc).__name__}: {exc}")

        return text_response(
            json.dumps(
                {"runner": runner.name, "test_ids": list(ids)},
                indent=2,
            )
        )
