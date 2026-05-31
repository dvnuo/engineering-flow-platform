"""Tool surface backed by EFP runtime built-ins only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .efp_runtime.tools.builtin import create_core_tool_registry
from .efp_runtime.tools.definition import ToolContext
from .efp_runtime.tools.registry import ToolRegistry
from .efp_runtime.tools.runtime import ToolRuntime
from .efp_runtime.types import ToolCall


@dataclass
class ToolResult:
    """Result from tool execution."""

    success: bool
    content: str = ""
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata or {})

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": self.success,
            "content": self.content,
            "error": self.error,
        }
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def __str__(self) -> str:
        if self.success:
            return self.content if self.content else "(no result)"
        if self.error:
            return f"Error: {self.error}"
        return self.content if self.content else "Error: Unknown (no details)"


class Tool:
    """Small wrapper around an EFP runtime tool definition."""

    def __init__(self, tool_id: str, description: str = "", input_schema: Optional[Dict[str, Any]] = None):
        self.name = tool_id
        self.id = tool_id
        self.description = description
        self.input_schema = dict(input_schema or {})

    async def execute(self, **kwargs: Any) -> ToolResult:
        return await execute_tool(self.name, **kwargs)


def _create_registry() -> ToolRegistry:
    return create_core_tool_registry(
        Path.cwd(),
    )


def _tool_registry() -> ToolRegistry:
    return _create_registry()


def _tool_wrappers() -> Dict[str, Tool]:
    return {
        tool.id: Tool(
            tool.id,
            description=tool.description,
            input_schema=dict(tool.input_schema or {}),
        )
        for tool in _tool_registry().list()
    }


TOOLS: Dict[str, Tool] = _tool_wrappers()


def get_all_tools() -> List[Dict[str, Any]]:
    """Get all EFP runtime builtin tool schemas."""
    return get_tools_schema()


def get_tool_names() -> List[str]:
    """Get all EFP runtime builtin tool names."""
    return [tool.id for tool in _tool_registry().list()]


def get_tool(name: str) -> Optional[Dict[str, Any]]:
    """Get one EFP runtime builtin tool schema by name."""
    return get_tool_map().get(name)


def get_tool_map() -> Dict[str, Dict[str, Any]]:
    """Get EFP runtime builtin tool schema map by tool name."""
    return {schema["function"]["name"]: schema for schema in get_tools_schema()}


def get_tools_schema() -> List[Dict[str, Any]]:
    """Get OpenAI function-tool schemas for EFP runtime builtin tools."""
    schemas: List[Dict[str, Any]] = []
    for tool in _tool_registry().list():
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.id,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema or {}),
                },
                "metadata": {
                    "tool_source": "efp_runtime",
                    "tool_id": tool.id,
                },
            }
        )
    return schemas


def _strip_runtime_metadata(kwargs: Dict[str, Any]) -> tuple[Dict[str, Any], ToolContext]:
    args = {key: value for key, value in kwargs.items() if value is not None}
    session_id = args.pop("_session_id", None)
    request_id = args.pop("_request_id", None)
    run_id = args.pop("_run_id", None)
    metadata = args.pop("_metadata", None)
    return args, ToolContext(
        session_id=str(session_id) if session_id else None,
        request_id=str(request_id) if request_id else None,
        run_id=str(run_id) if run_id else None,
        metadata=dict(metadata or {}) if isinstance(metadata, dict) else {},
    )


async def execute_tool(name: str, **kwargs: Any) -> ToolResult:
    """Execute an EFP runtime builtin tool by name."""
    args, context = _strip_runtime_metadata(dict(kwargs))
    registry = _tool_registry()
    runtime = ToolRuntime(registry)
    result = await runtime.execute(
        ToolCall(tool_name=name, arguments=args),
        context=context,
    )
    metadata = dict(result.metadata or {})
    metadata.update(
        {
            "tool_source": "efp_runtime",
            "tool_id": result.tool_name,
            "status": result.status,
            "truncated": result.truncated,
        }
    )
    return ToolResult(
        success=result.success,
        content=result.content,
        error=result.error,
        metadata=metadata,
    )


__all__ = [
    "ToolResult",
    "Tool",
    "TOOLS",
    "get_all_tools",
    "get_tool_names",
    "get_tool",
    "get_tool_map",
    "get_tools_schema",
    "execute_tool",
]
