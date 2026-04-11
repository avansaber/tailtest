"""tailtest.mcp.tools — the 7 MCP tools exposed by ``tailtest mcp-serve``.

Phase 1 Task 1.4. Each tool is implemented as a subclass of :class:`BaseTool`
in its own module. The tuple :data:`ALL_TOOLS` is consumed by the MCP server
to register tool definitions at startup and dispatch `tools/call` requests.

Tools:

- **scan_project**      — runs the ProjectScanner and returns a ProjectProfile
- **impacted_tests**    — returns the test IDs affected by a set of changed files
- **run_tests**         — executes tests and returns a FindingBatch
- **generate_tests**    — Phase 1 stub; Phase 1 Task 1.12b implements it in Checkpoint F
- **get_baseline**      — returns the current baseline summary
- **tailtest_status**   — returns current config, last run, and recommendations count
- **invoke_validator**  — Phase 5: spawns Jiminy Cricket validator subagent (thorough+)
"""

from tailtest.mcp.tools.base import BaseTool, ToolResponse
from tailtest.mcp.tools.generate_tests import GenerateTestsTool
from tailtest.mcp.tools.get_baseline import GetBaselineTool
from tailtest.mcp.tools.impacted_tests import ImpactedTestsTool
from tailtest.mcp.tools.invoke_validator import InvokeValidatorTool
from tailtest.mcp.tools.run_tests import RunTestsTool
from tailtest.mcp.tools.scan_project import ScanProjectTool
from tailtest.mcp.tools.tailtest_status import TailtestStatusTool

ALL_TOOLS: tuple[type[BaseTool], ...] = (
    ScanProjectTool,
    ImpactedTestsTool,
    RunTestsTool,
    GenerateTestsTool,
    GetBaselineTool,
    TailtestStatusTool,
    InvokeValidatorTool,
)

__all__ = [
    "ALL_TOOLS",
    "BaseTool",
    "GenerateTestsTool",
    "GetBaselineTool",
    "ImpactedTestsTool",
    "InvokeValidatorTool",
    "RunTestsTool",
    "ScanProjectTool",
    "TailtestStatusTool",
    "ToolResponse",
]
