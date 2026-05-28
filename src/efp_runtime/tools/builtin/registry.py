"""Factory for Runtime v2 core built-in tool registries."""

from __future__ import annotations

from pathlib import Path

from ...permissions import PermissionMetadata
from ..registry import ToolRegistry
from .apply_patch import create_apply_patch_tool
from .edit import create_edit_tool
from .filesystem import create_filesystem_tools, normalize_workspace_root
from .search import create_glob_tool, create_grep_tool
from .shell import create_shell_exec_tool
from .todo import create_todo_write_tool


def create_core_tool_registry(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    shell_permission: PermissionMetadata | None = None,
) -> ToolRegistry:
    """Create a registry containing Runtime v2 core built-in tools."""

    root = normalize_workspace_root(workspace_root)
    registry = ToolRegistry()
    registry.register(create_apply_patch_tool(root, permission=write_permission))
    registry.register(create_edit_tool(root, permission=write_permission))
    for tool in create_filesystem_tools(root, write_permission=write_permission):
        registry.register(tool)
    registry.register(create_glob_tool(root))
    registry.register(create_grep_tool(root))
    registry.register(create_shell_exec_tool(root, permission=shell_permission))
    registry.register(create_todo_write_tool())
    return registry
