"""tailtest.mcp — MCP server exposing tailtest tools to MCP-compatible clients.

Phase 0: empty scaffold. The server responds to ``initialize`` and
``tools/list`` with an empty tool list, and ``tools/call`` returns a
"not yet implemented" error for any call.

Phase 1 adds real tools: ``scan_project``, ``impacted_tests``, ``run_tests``,
``generate_tests``, ``get_baseline``, ``tailtest_status``.
"""
