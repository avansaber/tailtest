"""``run_tests`` MCP tool — executes tests and returns a structured FindingBatch."""

from __future__ import annotations

import json
from typing import Any, ClassVar
from uuid import uuid4

# Self-register runners at import time.
import tailtest.core.runner.javascript  # noqa: F401
import tailtest.core.runner.python  # noqa: F401
from tailtest.core.baseline import BaselineManager
from tailtest.core.config import ConfigLoader
from tailtest.core.findings.schema import FindingBatch
from tailtest.core.runner import RunnerNotAvailable, get_default_registry
from tailtest.mcp.tools.base import BaseTool, ToolResponse, error_response, text_response

# Audit gap #5: additionalContext truncation rule. Keep MCP tool output
# under 5 KB to stay well under the 10k-token soft limit. Overflow gets
# written to .tailtest/reports/latest.json and referenced in the response.
_MAX_TOOL_OUTPUT_BYTES = 5 * 1024


class RunTestsTool(BaseTool):
    name: ClassVar[str] = "run_tests"
    description: ClassVar[str] = (
        "Execute tests and return a structured FindingBatch. Pass an explicit "
        "list of test IDs (typically the output of impacted_tests) to run only "
        "those tests. Pass an empty list to run all tests. Returns a JSON "
        "FindingBatch with test results + any new failures (baseline findings "
        "are filtered out). Output is truncated to the top 5 findings if the "
        "full batch exceeds the MCP size budget; the full batch is always "
        "written to .tailtest/reports/latest.json."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "test_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Test IDs to run. Empty list runs all discovered tests.",
            },
            "timeout": {
                "type": "integer",
                "description": "Per-test-run timeout in seconds. Default 30.",
                "default": 30,
            },
        },
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        test_ids_raw = arguments.get("test_ids") or []
        if not isinstance(test_ids_raw, list):
            return error_response("`test_ids` must be a list of strings")
        timeout = int(arguments.get("timeout", 30))

        registry = get_default_registry()
        runners = registry.all_for_project(self.project_root)
        if not runners:
            empty_batch = FindingBatch(
                run_id=str(uuid4()),
                depth="standard",
                summary_line="tailtest: no runners detected for this project",
            )
            return text_response(empty_batch.model_dump_json(indent=2))

        # Phase 1: first matching runner. Phase 2 partitions by language.
        runner = runners[0]
        run_id = str(uuid4())
        try:
            batch = await runner.run(
                list(test_ids_raw),
                run_id=run_id,
                timeout_seconds=float(timeout),
            )
        except RunnerNotAvailable as exc:
            return error_response(f"{runner.name}: runner not available: {exc}")
        except TimeoutError:
            return error_response(f"{runner.name}: test run exceeded {timeout}s timeout")
        except Exception as exc:  # noqa: BLE001
            return error_response(f"{runner.name}.run() failed: {type(exc).__name__}: {exc}")

        # Apply the baseline filter so known-debt findings don't surface.
        tailtest_dir = self.project_root / ".tailtest"
        manager = BaselineManager(tailtest_dir)
        filtered = manager.apply_to(batch)

        # Load config to stamp the current depth onto the batch.
        config = ConfigLoader(tailtest_dir).load()
        filtered = filtered.model_copy(update={"depth": config.depth.value})

        # Always write the full batch to disk for truncation fallback.
        reports_dir = tailtest_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        latest_path = reports_dir / "latest.json"
        full_json = filtered.model_dump_json(indent=2)
        latest_path.write_text(full_json, encoding="utf-8")

        # Audit gap #5: truncate if the full JSON is too big for MCP.
        if len(full_json.encode("utf-8")) > _MAX_TOOL_OUTPUT_BYTES:
            new_findings = filtered.new_findings
            new_findings.sort(key=lambda f: (-f.severity.rank, str(f.file), f.line))
            top_5 = new_findings[:5]
            truncated_batch = filtered.model_copy(update={"findings": top_5})
            payload = json.loads(truncated_batch.model_dump_json())
            payload["_truncated"] = True
            payload["_full_batch_path"] = str(latest_path)
            payload["_total_new_findings"] = len(new_findings)
            return text_response(json.dumps(payload, indent=2))

        return text_response(full_json)
