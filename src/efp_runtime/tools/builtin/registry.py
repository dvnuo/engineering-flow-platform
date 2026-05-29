"""Factory for Runtime v2 core built-in tool registries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...instructions import ReadInstructionResolver
from ...lsp import LSPClient
from ...permissions import ALLOW, PermissionMetadata
from ...questions import QuestionBroker
from ...skills.discovery import SkillDiscovery
from ...skills.tool import build_skill_list_tool, build_skill_tool
from ..registry import ToolRegistry
from .apply_patch import create_apply_patch_tool
from .background_shell import (
    DEFAULT_MAX_BUFFER_BYTES,
    ShellJobManager,
    create_shell_kill_tool,
    create_shell_status_tool,
)
from .edit import create_edit_tool
from .fetch import create_fetch_tool, create_webfetch_tool
from .filesystem import (
    create_list_dir_tool,
    create_read_file_tool,
    create_read_tool,
    create_write_file_tool,
    create_write_tool,
    normalize_workspace_root,
)
from .invalid import create_invalid_tool
from .lsp import create_lsp_tool
from .plan import create_plan_exit_tool
from .question import create_question_tool
from .repository import create_repo_clone_tool, create_repo_overview_tool
from .search import create_glob_tool, create_grep_tool
from .shell import create_bash_tool, create_shell_exec_tool
from .task import (
    TaskToolRunner,
    create_task_cancel_tool,
    create_task_status_tool,
    create_task_tool,
)
from .todo import TodoStore, create_todo_write_tool, create_todowrite_tool

if TYPE_CHECKING:
    from ...agents.background_tasks import BackgroundTaskManager


def create_core_tool_registry(
    workspace_root: str | Path,
    *,
    tool_surface: str = "opencode",
    include_legacy_aliases: bool = False,
    write_permission: PermissionMetadata | None = None,
    shell_permission: PermissionMetadata | None = None,
    fetch_permission: PermissionMetadata | None = None,
    shell_job_manager: ShellJobManager | None = None,
    enable_background_shell: bool = True,
    background_shell_max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
    task_runner: TaskToolRunner | None = None,
    include_task_tool: bool = False,
    allow_background_task: bool = False,
    background_task_manager: "BackgroundTaskManager | None" = None,
    question_broker: QuestionBroker | None = None,
    include_question_tool: bool = False,
    skill_discovery: SkillDiscovery | None = None,
    skill_directories: Iterable[str | Path] | None = None,
    include_skill_tool: bool = False,
    include_skill_list_tool: bool | None = None,
    skill_permission: PermissionMetadata | None = None,
    skill_list_permission: PermissionMetadata | None = None,
    tool_permissions: Mapping[str, Any] | None = None,
    max_skill_sidecar_chars: int = 4000,
    instruction_resolver: ReadInstructionResolver | None = None,
    lsp_client: LSPClient | None = None,
    include_lsp_tool: bool = False,
    lsp_permission: PermissionMetadata | None = None,
    include_plan_tool: bool = False,
) -> ToolRegistry:
    """Create a registry containing Runtime v2 core built-in tools."""

    root = normalize_workspace_root(workspace_root)
    resolved_surface = _normalize_tool_surface(tool_surface)
    expose_legacy_aliases = (
        bool(include_legacy_aliases) or resolved_surface == "legacy"
    )
    shell_background_manager = (
        shell_job_manager
        if enable_background_shell
        else None
    )
    if enable_background_shell and shell_background_manager is None:
        shell_background_manager = ShellJobManager(
            max_buffer_bytes=background_shell_max_buffer_bytes
        )
    resolved_skill_discovery = _resolve_skill_discovery(
        skill_discovery=skill_discovery,
        skill_directories=skill_directories,
        include_skill_tool=include_skill_tool,
    )
    skill_list_discovery = _resolve_skill_list_discovery(
        skill_discovery=skill_discovery,
        skill_directories=skill_directories,
        include_skill_list_tool=(
            include_skill_list_tool if expose_legacy_aliases else False
        ),
        resolved_skill_discovery=resolved_skill_discovery,
    )
    registry = ToolRegistry()
    registry.register(create_apply_patch_tool(root, permission=write_permission))
    registry.register(create_edit_tool(root, permission=write_permission))
    if expose_legacy_aliases:
        registry.register(create_fetch_tool(permission=fetch_permission))
    registry.register(create_webfetch_tool(permission=fetch_permission))
    registry.register(
        create_read_tool(root, instruction_resolver=instruction_resolver)
    )
    if expose_legacy_aliases:
        registry.register(
            create_read_file_tool(root, instruction_resolver=instruction_resolver)
        )
        registry.register(create_list_dir_tool(root))
    registry.register(create_write_tool(root, permission=write_permission))
    if expose_legacy_aliases:
        registry.register(create_write_file_tool(root, permission=write_permission))
    registry.register(create_glob_tool(root))
    registry.register(create_grep_tool(root))
    registry.register(create_invalid_tool())
    registry.register(create_repo_clone_tool(root))
    registry.register(create_repo_overview_tool(root))
    if include_lsp_tool or lsp_client is not None:
        registry.register(
            create_lsp_tool(root, client=lsp_client, permission=lsp_permission)
        )
    if expose_legacy_aliases:
        registry.register(
            create_shell_exec_tool(
                root,
                permission=shell_permission,
                shell_job_manager=shell_background_manager,
                enable_background=enable_background_shell,
            )
        )
    registry.register(
        create_bash_tool(
            root,
            permission=shell_permission,
            shell_job_manager=shell_background_manager,
            enable_background=enable_background_shell,
        )
    )
    if expose_legacy_aliases and shell_background_manager is not None:
        registry.register(create_shell_status_tool(shell_background_manager, root))
        registry.register(create_shell_kill_tool(shell_background_manager, root))
    if task_runner is not None or include_task_tool:
        if task_runner is None:
            raise ValueError("task_runner is required when include_task_tool is true.")
        task_manager = background_task_manager
        if allow_background_task and task_manager is None:
            from ...agents.background_tasks import BackgroundTaskManager

            task_manager = BackgroundTaskManager()
        registry.register(
            create_task_tool(
                task_runner,
                allow_background=allow_background_task,
                background_manager=task_manager,
            )
        )
        if expose_legacy_aliases and allow_background_task and task_manager is not None:
            registry.register(create_task_status_tool(task_manager))
            registry.register(create_task_cancel_tool(task_manager))
    if include_question_tool:
        registry.register(create_question_tool(question_broker))
    todo_store: TodoStore = {}
    if expose_legacy_aliases:
        registry.register(create_todo_write_tool(todos_by_session=todo_store))
    registry.register(create_todowrite_tool(todos_by_session=todo_store))
    if include_plan_tool:
        registry.register(create_plan_exit_tool())
    if resolved_skill_discovery is not None:
        registry.register(
            build_skill_tool(
                resolved_skill_discovery,
                max_sidecar_chars=max_skill_sidecar_chars,
                permission=skill_permission or _default_skill_permission(
                    subject_arg="name"
                ),
                tool_permissions=tool_permissions,
            )
        )
    if skill_list_discovery is not None:
        registry.register(
            build_skill_list_tool(
                skill_list_discovery,
                permission=skill_list_permission or _default_skill_permission(),
                tool_permissions=tool_permissions,
            )
        )
    return registry


def _normalize_tool_surface(tool_surface: str) -> str:
    surface = str(tool_surface).strip()
    if surface not in {"opencode", "legacy"}:
        raise ValueError("tool_surface must be 'opencode' or 'legacy'")
    return surface


def _resolve_skill_discovery(
    *,
    skill_discovery: SkillDiscovery | None,
    skill_directories: Iterable[str | Path] | None,
    include_skill_tool: bool,
) -> SkillDiscovery | None:
    if skill_discovery is not None:
        return skill_discovery
    if skill_directories is not None:
        directories = list(skill_directories)
        if directories:
            return SkillDiscovery(directories)
    if include_skill_tool:
        return SkillDiscovery([])
    return None


def _resolve_skill_list_discovery(
    *,
    skill_discovery: SkillDiscovery | None,
    skill_directories: Iterable[str | Path] | None,
    include_skill_list_tool: bool | None,
    resolved_skill_discovery: SkillDiscovery | None,
) -> SkillDiscovery | None:
    if include_skill_list_tool is False:
        return None
    if resolved_skill_discovery is not None:
        return resolved_skill_discovery
    if skill_discovery is not None:
        return skill_discovery
    if skill_directories is not None:
        directories = list(skill_directories)
        if directories:
            return SkillDiscovery(directories)
    if include_skill_list_tool is True:
        return SkillDiscovery([])
    return None


def _default_skill_permission(*, subject_arg: str | None = None) -> PermissionMetadata:
    data = {}
    if subject_arg is not None:
        data["subject_arg"] = subject_arg
    return PermissionMetadata(
        action=ALLOW,
        category="skill",
        resource="context",
        risk="low",
        data=data,
    )
