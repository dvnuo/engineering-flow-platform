"""Factory for Runtime v2 core built-in tool registries."""

from __future__ import annotations

from pathlib import Path

from ...permissions import PermissionMetadata
from ..registry import ToolRegistry
from .filesystem import create_filesystem_tools, normalize_workspace_root
from .search import create_grep_tool
from .shell import create_shell_exec_tool


def create_core_tool_registry(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    shell_permission: PermissionMetadata | None = None,
) -> ToolRegistry:
    """Create a registry containing Runtime v2 core built-in tools."""

    root = normalize_workspace_root(workspace_root)
    registry = ToolRegistry()
    for tool in create_filesystem_tools(root, write_permission=write_permission):
        registry.register(tool)
    registry.register(create_grep_tool(root))
    registry.register(create_shell_exec_tool(root, permission=shell_permission))
    return registry
