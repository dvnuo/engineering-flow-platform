"""Factory for EFP runtime core built-in tool registries."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...connector_bridge import ConnectorBridgeBroker
from ...instructions import ReadInstructionResolver
from ...lsp import LSPClient
from ...permissions import ALLOW, PermissionMetadata
from ...questions import QuestionBroker
from ...session.protocol import SessionStore
from ...session.store import InMemorySessionStore
from ...session.todo import SessionTodoStore
from ...skills.discovery import SkillDiscovery
from ...skills.tool import build_skill_tool
from ..registry import ToolRegistry
from .apply_patch import create_apply_patch_tool
from .browser import create_browser_tool
from .edit import create_edit_tool
from .fetch import create_webfetch_tool
from .filesystem import (
    create_read_tool,
    create_write_tool,
    normalize_read_roots,
    normalize_workspace_root,
)
from .invalid import create_invalid_tool
from .lsp import create_lsp_tool
from .plan import create_plan_exit_tool
from .question import create_question_tool
from .repository import create_repo_clone_tool, create_repo_overview_tool
from .search import create_glob_tool, create_grep_tool
from .session_search import create_session_search_tool
from .shell import create_bash_tool
from .task import TaskToolRequest, TaskToolResult, TaskToolRunner, create_task_tool
from .todo import create_todowrite_tool
from .websearch import WebSearchRunner, create_websearch_tool

if TYPE_CHECKING:
    from ...agents.background_tasks import BackgroundTaskManager


def create_core_tool_registry(
    workspace_root: str | Path,
    *,
    write_permission: PermissionMetadata | None = None,
    shell_permission: PermissionMetadata | None = None,
    fetch_permission: PermissionMetadata | None = None,
    websearch_runner: WebSearchRunner | None = None,
    include_websearch_tool: bool = False,
    websearch_permission: PermissionMetadata | None = None,
    task_runner: TaskToolRunner | None = None,
    include_task_tool: bool = True,
    allow_background_task: bool = False,
    background_task_manager: "BackgroundTaskManager | None" = None,
    question_broker: QuestionBroker | None = None,
    include_question_tool: bool = False,
    skill_discovery: SkillDiscovery | None = None,
    skill_directories: Iterable[str | Path] | None = None,
    include_skill_tool: bool = True,
    skill_permission: PermissionMetadata | None = None,
    tool_permissions: Mapping[str, Any] | None = None,
    max_skill_sidecar_chars: int = 4000,
    instruction_resolver: ReadInstructionResolver | None = None,
    lsp_client: LSPClient | None = None,
    include_lsp_tool: bool = False,
    lsp_permission: PermissionMetadata | None = None,
    include_plan_tool: bool = False,
    include_repository_tools: bool = False,
    todo_store: SessionTodoStore | None = None,
    include_browser_tool: bool = False,
    connector_bridge: ConnectorBridgeBroker | None = None,
    connector_event_publisher: Any = None,
    read_roots: Iterable[str | Path] | None = None,
    include_session_search_tool: bool = False,
    session_store: SessionStore | None = None,
    session_search_permission: PermissionMetadata | None = None,
) -> ToolRegistry:
    """Create a registry containing EFP runtime core built-in tools.

    Skill directories (and any explicit ``read_roots``) become read-only roots
    for ``read``, ``glob`` and ``grep``: a skill may keep whatever layout it
    likes outside the workspace, and the model can still open its files.

    ``session_search`` reads the sessions of ``session_store``; production
    callers pass the runtime's own store so the tool sees the same sessions the
    gateway lists. Without one it falls back to an empty in-memory store.
    """

    root = normalize_workspace_root(workspace_root)
    resolved_skill_discovery = _resolve_skill_discovery(
        skill_discovery=skill_discovery,
        skill_directories=skill_directories,
        include_skill_tool=include_skill_tool,
    )
    resolved_read_roots = _resolve_read_roots(root, read_roots, resolved_skill_discovery)
    registry = ToolRegistry()
    registry.register(create_apply_patch_tool(root, permission=write_permission))
    registry.register(create_edit_tool(root, permission=write_permission))
    registry.register(create_webfetch_tool(permission=fetch_permission))
    if websearch_runner is not None or include_websearch_tool:
        if websearch_runner is None:
            raise ValueError(
                "websearch_runner is required when include_websearch_tool is true."
            )
        registry.register(
            create_websearch_tool(
                websearch_runner,
                permission=websearch_permission,
            )
        )
    registry.register(
        create_read_tool(
            root,
            instruction_resolver=instruction_resolver,
            read_roots=resolved_read_roots,
        )
    )
    registry.register(create_write_tool(root, permission=write_permission))
    registry.register(create_glob_tool(root, read_roots=resolved_read_roots))
    registry.register(create_grep_tool(root, read_roots=resolved_read_roots))
    registry.register(create_invalid_tool())
    if include_repository_tools:
        registry.register(create_repo_clone_tool(root))
        registry.register(create_repo_overview_tool(root))
    if include_lsp_tool or lsp_client is not None:
        registry.register(
            create_lsp_tool(root, client=lsp_client, permission=lsp_permission)
        )
    registry.register(create_bash_tool(root, permission=shell_permission))
    if include_task_tool:
        resolved_task_runner = task_runner or _missing_task_runner
        task_manager = background_task_manager
        if allow_background_task and task_manager is None:
            from ...agents.background_tasks import BackgroundTaskManager

            task_manager = BackgroundTaskManager()
        registry.register(
            create_task_tool(
                resolved_task_runner,
                allow_background=allow_background_task,
                background_manager=task_manager,
            )
        )
    if include_question_tool:
        registry.register(create_question_tool(question_broker))
    if include_browser_tool:
        registry.register(
            create_browser_tool(
                connector_bridge,
                event_publisher=connector_event_publisher,
            )
        )
    resolved_todo_store = todo_store or SessionTodoStore()
    registry.register(create_todowrite_tool(todo_store=resolved_todo_store))
    if include_session_search_tool:
        registry.register(
            create_session_search_tool(
                session_store if session_store is not None else InMemorySessionStore(),
                permission=session_search_permission,
            )
        )
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
    return registry


async def _missing_task_runner(request: TaskToolRequest) -> TaskToolResult:
    return TaskToolResult(
        task_id=request.task_id,
        text=(
            "No task runner is configured for this registry. "
            "Use AgentRuntime to run subagent tasks."
        ),
        state="error",
        metadata={"configured": False},
    )


def _resolve_read_roots(
    workspace_root: Path,
    read_roots: Iterable[str | Path] | None,
    skill_discovery: SkillDiscovery | None,
) -> tuple[Path, ...]:
    """Explicit read roots plus every configured skill directory that exists."""

    candidates: list[str | Path] = list(read_roots or [])
    if skill_discovery is not None:
        candidates.extend(skill_discovery.directories)
    return normalize_read_roots(workspace_root, candidates)


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
