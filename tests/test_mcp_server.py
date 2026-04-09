"""End-to-end tests for the MCP server handling tool dispatch (Phase 1 Task 1.4)."""

from __future__ import annotations

import json

import pytest

from tailtest.mcp.server import TailtestMCPServer


@pytest.mark.asyncio
async def test_tools_list_returns_six_tools() -> None:
    server = TailtestMCPServer()
    raw = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
    response = await server.handle_message(raw)
    assert response is not None
    data = json.loads(response)
    assert "result" in data
    tools = data["result"]["tools"]
    assert len(tools) == 6
    names = {t["name"] for t in tools}
    assert names == {
        "scan_project",
        "impacted_tests",
        "run_tests",
        "generate_tests",
        "get_baseline",
        "tailtest_status",
    }


@pytest.mark.asyncio
async def test_tools_call_dispatches_to_tailtest_status() -> None:
    server = TailtestMCPServer()
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "tailtest_status", "arguments": {}},
        }
    )
    response = await server.handle_message(raw)
    assert response is not None
    data = json.loads(response)
    assert "result" in data
    result = data["result"]
    assert result["isError"] is False
    assert "content" in result
    # The tool response text is itself a JSON doc
    inner = json.loads(result["content"][0]["text"])
    assert "tailtest_version" in inner


@pytest.mark.asyncio
async def test_tools_call_unknown_tool_returns_isError_true() -> None:
    server = TailtestMCPServer()
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
    )
    response = await server.handle_message(raw)
    data = json.loads(response or "{}")
    result = data["result"]
    assert result["isError"] is True
    assert "unknown tool" in result["content"][0]["text"].lower()


@pytest.mark.asyncio
async def test_tools_call_with_non_dict_arguments_returns_isError_true() -> None:
    server = TailtestMCPServer()
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "scan_project", "arguments": "not a dict"},
        }
    )
    response = await server.handle_message(raw)
    data = json.loads(response or "{}")
    result = data["result"]
    assert result["isError"] is True


@pytest.mark.asyncio
async def test_initialize_still_returns_serverinfo() -> None:
    """The Phase 0 initialize handshake should still work post-tool-wiring."""
    server = TailtestMCPServer()
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
    )
    response = await server.handle_message(raw)
    data = json.loads(response or "{}")
    assert data["result"]["serverInfo"]["name"] == "tailtest"
    assert data["result"]["capabilities"]["tools"]["listChanged"] is False
