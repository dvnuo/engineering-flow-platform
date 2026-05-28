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
from .task import TaskToolRunner, create_task_tool
from .todo import create_todo_write_tool


def create_core_tool_registry(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    shell_permission: PermissionMetadata | None = None,
    task_runner: TaskToolRunner | None = None,
    include_task_tool: bool = False,
    allow_background_task: bool = False,
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
    if task_runner is not None or include_task_tool:
        if task_runner is None:
            raise ValueError("task_runner is required when include_task_tool is true.")
        registry.register(
            create_task_tool(task_runner, allow_background=allow_background_task)
        )
    registry.register(create_todo_write_tool())
    return registry
