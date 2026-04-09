"""BaseTool — shared abstract class for every tailtest MCP tool.

Each tool subclass sets ``name``, ``description``, and ``input_schema``
class attributes + implements ``invoke(arguments)``. The MCP server
calls ``.definition()`` at startup to register the tool and calls
``invoke()`` when a client sends ``tools/call``.

Tool responses follow the MCP spec: ``{content: [{type: "text", text: ...}], isError: bool}``.
Helper constructors are provided so tools don't have to reconstruct the
envelope themselves.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, TypedDict

logger = logging.getLogger(__name__)


class ToolContentBlock(TypedDict):
    """One content block inside an MCP tool response."""

    type: str
    text: str


class ToolResponse(TypedDict):
    """The full MCP tool response envelope."""

    content: list[ToolContentBlock]
    isError: bool


def text_response(text: str, *, is_error: bool = False) -> ToolResponse:
    """Build a one-block text response."""
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def error_response(message: str) -> ToolResponse:
    """Build an error response — ``isError: true``, single text block."""
    return text_response(message, is_error=True)


class BaseTool(ABC):
    """Abstract base class every tailtest MCP tool implements.

    Subclasses must set the three class attributes and implement ``invoke``.
    """

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    input_schema: ClassVar[dict[str, Any]] = {"type": "object", "properties": {}}

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    @classmethod
    def definition(cls) -> dict[str, Any]:
        """The MCP tool schema returned by ``tools/list``."""
        if not cls.name:
            raise ValueError(f"{cls.__name__} has no `name` class attribute")
        return {
            "name": cls.name,
            "description": cls.description,
            "inputSchema": cls.input_schema,
        }

    @abstractmethod
    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        """Execute the tool and return an MCP response envelope."""
        raise NotImplementedError
