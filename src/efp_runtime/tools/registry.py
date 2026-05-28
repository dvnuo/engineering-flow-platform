"""Registry for EFP Runtime v2 tools."""

from __future__ import annotations

from collections.abc import Iterable

from .definition import ToolDef


class ToolRegistry:
    """In-memory registry keyed by tool id."""

    def __init__(self, tools: Iterable[ToolDef] | None = None):
        self._tools: dict[str, ToolDef] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: ToolDef, *, replace: bool = False) -> ToolDef:
        if not tool.id:
            raise ValueError("Tool id is required.")
        if tool.id in self._tools and not replace:
            raise ValueError(f"Tool already registered: {tool.id}")
        self._tools[tool.id] = tool
        return tool

    def get(self, tool_id: str) -> ToolDef | None:
        return self._tools.get(tool_id)

    def require(self, tool_id: str) -> ToolDef:
        tool = self.get(tool_id)
        if tool is None:
            raise KeyError(f"Unknown tool: {tool_id}")
        return tool

    def list(self) -> list[ToolDef]:
        return [self._tools[key] for key in sorted(self._tools)]

    def ids(self) -> list[str]:
        return sorted(self._tools)
