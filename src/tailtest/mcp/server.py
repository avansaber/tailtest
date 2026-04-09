"""MCP server — JSON-RPC 2.0 over stdio scaffold.

Phase 1: responds to ``initialize``, ``tools/list`` (6 tools), and
``tools/call`` (dispatches to the registered tool implementations in
:mod:`tailtest.mcp.tools`).

All logging goes to stderr so stdout stays clean for the MCP protocol.

The transport loop preserves the v0.3.1 Feynman-case-study fix for
non-tty stdin (``connect_read_pipe`` → ``asyncio.to_thread`` fallback).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import Any

from tailtest import __version__
from tailtest.mcp.tools import ALL_TOOLS

# Logging goes to stderr — stdout is reserved for MCP protocol messages.
logger = logging.getLogger("tailtest.mcp")


# Phase 1: the full tool list from tailtest.mcp.tools. Computed once at
# module load time; the set is static per version so there's no cost to
# rebuilding it per request.
_TOOL_DEFINITIONS: list[dict[str, Any]] = [cls.definition() for cls in ALL_TOOLS]
_TOOL_CLASS_BY_NAME: dict[str, type] = {cls.name: cls for cls in ALL_TOOLS}


# JSON-RPC 2.0 standard error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


class TailtestMCPServer:
    """Minimal Phase 0 MCP server.

    Implements just enough of the Model Context Protocol for a client to:
    1. Connect and see server info
    2. List available tools (empty in Phase 0)
    3. Attempt to call a tool (gets "not implemented" back)
    """

    def __init__(self) -> None:
        self.initialized = False
        self._sampling_request_id = 0

    # --- Message handling -------------------------------------------------

    async def handle_message(self, raw: str) -> str | None:
        """Parse a single JSON-RPC message and return a response string (or None)."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON from client: %s", exc)
            return _error_response(None, _PARSE_ERROR, f"Parse error: {exc}")

        if not isinstance(msg, dict):
            return _error_response(None, _INVALID_REQUEST, "Request must be a JSON object")

        msg_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params", {}) or {}

        if method is None:
            return _error_response(msg_id, _INVALID_REQUEST, "Missing 'method' field")

        try:
            if method == "initialize":
                return _success_response(msg_id, self._handle_initialize(params))

            if method == "initialized" or method == "notifications/initialized":
                # Notification from client that it has processed our initialize
                # response. No reply required for notifications.
                self.initialized = True
                return None

            if method == "tools/list":
                return _success_response(msg_id, self._handle_tools_list())

            if method == "tools/call":
                return _success_response(msg_id, await self._handle_tool_call(params))

            if method == "shutdown":
                return _success_response(msg_id, None)

            # Any other method is unknown in Phase 0.
            return _error_response(msg_id, _METHOD_NOT_FOUND, f"Method not found: {method}")

        except Exception:
            logger.error("Unhandled error in method %r:\n%s", method, traceback.format_exc())
            return _error_response(msg_id, _INTERNAL_ERROR, "Internal server error")

    # --- Method handlers --------------------------------------------------

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Respond to the client's ``initialize`` call.

        Returns server info + capabilities. Phase 0 advertises tool support
        (with an empty list) so clients know the capability exists even if
        no tools are registered yet.
        """
        return {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": "tailtest",
                "version": __version__,
            },
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        """Return the list of available tools.

        Phase 0: empty list. Phase 1 populates this with real tools.
        """
        return {"tools": _TOOL_DEFINITIONS}

    async def _handle_tool_call(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call.

        Phase 1: dispatches to the registered tool implementations. Each
        tool catches its own exceptions and returns a proper error
        envelope — this outer handler only catches truly unexpected
        failures (e.g. malformed params, unknown tool name).
        """
        tool_name = params.get("name", "<unknown>")
        arguments = params.get("arguments") or {}

        if not isinstance(arguments, dict):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"tailtest: tool arguments must be an object, got {type(arguments).__name__}",
                    }
                ],
                "isError": True,
            }

        tool_cls = _TOOL_CLASS_BY_NAME.get(tool_name)
        if tool_cls is None:
            known = ", ".join(sorted(_TOOL_CLASS_BY_NAME.keys()))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"tailtest v{__version__}: unknown tool '{tool_name}'. "
                            f"Known tools: {known}"
                        ),
                    }
                ],
                "isError": True,
            }

        # Tools run with cwd as the project root by default. The MCP server
        # is typically invoked by the Claude Code plugin from the user's
        # working project, so Path.cwd() is the right default.
        project_root = Path.cwd()
        try:
            tool = tool_cls(project_root)
            response = await tool.invoke(arguments)
        except Exception:  # noqa: BLE001
            logger.error("tool %r raised:\n%s", tool_name, traceback.format_exc())
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"tailtest: tool '{tool_name}' failed unexpectedly (see server logs)",
                    }
                ],
                "isError": True,
            }

        return dict(response)

    # --- Server loop ------------------------------------------------------

    async def run(self) -> None:
        """Read JSON-RPC messages from stdin, write responses to stdout.

        Uses an async reader when stdin is a pipe/tty. Falls back to
        blocking reads on a worker thread when ``connect_read_pipe`` fails
        (v0.3.1 Feynman fix — some test harnesses inherit non-tty stdin and
        ``connect_read_pipe`` raises ``OSError`` / ``ValueError`` in that case).
        """
        logger.info("tailtest MCP server starting on stdio")

        reader: asyncio.StreamReader | None = None
        try:
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)
        except (OSError, ValueError) as exc:
            logger.warning("connect_read_pipe failed (%s); falling back to blocking stdin", exc)
            reader = None

        while True:
            try:
                if reader is not None:
                    line_bytes = await reader.readline()
                else:
                    line_bytes = await asyncio.to_thread(sys.stdin.buffer.readline)

                if not line_bytes:
                    logger.info("stdin closed, shutting down")
                    break

                raw = line_bytes.decode("utf-8").strip()
                if not raw:
                    continue

                response = await self.handle_message(raw)
                if response is not None:
                    sys.stdout.write(response + "\n")
                    sys.stdout.flush()

            except KeyboardInterrupt:
                logger.info("interrupted, shutting down")
                break
            except Exception:
                logger.error("Unhandled error in server loop:\n%s", traceback.format_exc())


# --- JSON-RPC helpers -----------------------------------------------------


def _success_response(msg_id: Any, result: Any) -> str:
    """Build a JSON-RPC 2.0 success response."""
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error_response(msg_id: Any, code: int, message: str) -> str:
    """Build a JSON-RPC 2.0 error response."""
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


# --- Entry point ----------------------------------------------------------


def main() -> None:
    """Start the MCP server. Called by ``tailtest mcp-serve``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    server = TailtestMCPServer()
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == "__main__":
    main()
