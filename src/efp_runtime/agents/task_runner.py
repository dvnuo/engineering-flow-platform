"""Foreground subagent task runner for EFP runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from html import escape
import json
from pathlib import Path
import re
from typing import Any

from ..loop import LoopStatus
from ..permissions import (
    AGENT_PERMISSION_OVERLAY_METADATA_KEY,
    AGENT_PERMISSION_OVERLAY_SOURCE,
    AGENT_PERMISSION_OVERLAY_SOURCE_KEY,
    is_permission_subject_visible,
    merge_tool_permission_configs,
    normalize_agent_permission_overlay,
)
from ..runtime import AgentRuntime, RuntimeConfig
from ..session.models import Message, MessagePartType
from ..session.protocol import SessionStore
from ..session.store import InMemorySessionStore
from ..tools.builtin.task import (
    DEFAULT_TASK_TOOL_DESCRIPTION,
    TaskToolRequest,
    TaskToolResult,
    TaskToolRunner,
    create_task_tool,
)
from ..tools.definition import ToolDef
from ..tools.runtime import ToolRuntime
from .background_tasks import BackgroundTaskManager
from .profile import AgentProfile
from .registry import AgentRegistry


EMPTY_SUBAGENT_RESULT_MESSAGE = "Subagent completed without assistant text."
MANUAL_SUBAGENT_DESCRIPTION = (
    "This subagent should only be called manually by the user."
)
_PRIMARY_TASK_PROFILE_NAMES = {"build", "plan"}
_PRIMARY_TASK_PROFILE_MODES = {"primary", "build", "plan"}
_SUBAGENT_TASK_TOOL_IDS = frozenset({"task"})
_SUBAGENT_TODO_WRITE_TOOL_IDS = frozenset({"todowrite"})
_SUBAGENT_GUARD_ALLOW_ACTIONS = frozenset({"allow", "ask"})
_MUTATING_FILE_PERMISSION_ALIASES = {
    "edit": frozenset({"write", "edit", "apply_patch"}),
}


@dataclass(frozen=True)
class SubagentRunResult:
    """Summary of a EFP runtime child run launched by the task runner."""

    task_id: str
    profile_name: str
    child_session_id: str
    status: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


def create_subagent_task_runner(
    *,
    provider,
    workspace_root: str | Path | None = None,
    profiles: AgentRegistry | Iterable[AgentProfile] | None = None,
    base_config: RuntimeConfig | None = None,
    store_factory: Callable[[], SessionStore] | None = None,
    tool_runtime_factory: Callable[[AgentProfile], ToolRuntime] | None = None,
    session_id_prefix: str = "subagent",
) -> TaskToolRunner:
    """Create a task runner that executes foreground tasks in child runs."""

    if provider is None:
        raise ValueError("provider is required")

    registry = _resolve_registry(profiles)

    async def run_subagent(request: TaskToolRequest) -> TaskToolResult:
        try:
            profile = registry.resolve(request.subagent_type)
            child_session_id = _child_session_id(
                prefix=session_id_prefix,
                parent_session_id=request.session_id,
                task_id=request.task_id,
            )
            child_metadata = _child_metadata(
                request=request,
                profile=profile,
                child_session_id=child_session_id,
            )
            child_config = _child_config(
                profile=profile,
                base_config=base_config,
                workspace_root=workspace_root,
                metadata=child_metadata,
            )
            runtime = AgentRuntime(
                provider=provider,
                config=child_config,
                store=_make_store(store_factory),
                tool_runtime=_make_tool_runtime(tool_runtime_factory, profile),
            )
            result = await runtime.run(
                _child_prompt(profile, request),
                session_id=child_session_id,
                metadata=child_metadata,
                tools=_child_tool_overrides(
                    profile,
                    available_tool_ids=runtime.tool_runtime.registry.ids(),
                    parent_tool_permissions=(
                        None if base_config is None else base_config.tool_permissions
                    ),
                ),
            )
            text = _assistant_text(result.final_assistant_message)
            if not text and result.status != LoopStatus.COMPLETED:
                text = _assistant_error_text(result.final_assistant_message)
            if not text and result.status == LoopStatus.ERROR:
                text = _runtime_error_text(result.runtime_events)
            run_result = SubagentRunResult(
                task_id=request.task_id,
                profile_name=profile.name,
                child_session_id=result.session_id,
                status=result.status,
                text=text or EMPTY_SUBAGENT_RESULT_MESSAGE,
                metadata={
                    **child_metadata,
                    "child_session_id": result.session_id,
                    "child_status": result.status,
                    "status": result.status,
                    "child_iterations": result.iterations,
                    "profile": profile.name,
                },
            )
            if result.status != LoopStatus.COMPLETED:
                return TaskToolResult(
                    task_id=request.task_id,
                    text=_non_completed_text(run_result),
                    state="error",
                    metadata=run_result.metadata,
                )
            return TaskToolResult(
                task_id=request.task_id,
                text=run_result.text,
                state="completed",
                metadata=run_result.metadata,
            )
        except Exception as exc:  # noqa: BLE001 - task runners must not break parent loop.
            return TaskToolResult(
                task_id=request.task_id,
                text=_exception_text(exc),
                state="error",
                metadata={
                    "parent_session_id": request.session_id,
                    "task_id": request.task_id,
                    "subagent_type": request.subagent_type,
                    "child_status": LoopStatus.ERROR,
                    "status": LoopStatus.ERROR,
                    "error_type": exc.__class__.__name__,
                },
            )

    return run_subagent


def create_agent_task_tool(
    *,
    provider,
    workspace_root: str | Path | None = None,
    profiles: AgentRegistry | Iterable[AgentProfile] | None = None,
    base_config: RuntimeConfig | None = None,
    store_factory: Callable[[], SessionStore] | None = None,
    tool_runtime_factory: Callable[[AgentProfile], ToolRuntime] | None = None,
    session_id_prefix: str = "subagent",
    tool_id: str = "task",
    allow_background: bool = False,
    background_manager: BackgroundTaskManager | None = None,
) -> ToolDef:
    """Create a EFP runtime task tool backed by the subagent task runner."""

    if provider is None:
        raise ValueError("provider is required")
    registry = _resolve_registry(profiles)
    return create_task_tool(
        create_subagent_task_runner(
            provider=provider,
            workspace_root=workspace_root,
            profiles=registry,
            base_config=base_config,
            store_factory=store_factory,
            tool_runtime_factory=tool_runtime_factory,
            session_id_prefix=session_id_prefix,
        ),
        tool_id=tool_id,
        description=create_agent_task_tool_description(
            registry,
            tool_permissions=(
                None if base_config is None else base_config.tool_permissions
            ),
        ),
        allow_background=allow_background,
        background_manager=background_manager,
    )


def create_agent_task_tools(
    *,
    provider,
    workspace_root: str | Path | None = None,
    profiles: AgentRegistry | Iterable[AgentProfile] | None = None,
    base_config: RuntimeConfig | None = None,
    store_factory: Callable[[], SessionStore] | None = None,
    tool_runtime_factory: Callable[[AgentProfile], ToolRuntime] | None = None,
    session_id_prefix: str = "subagent",
    tool_id: str = "task",
    allow_background: bool = False,
    background_manager: BackgroundTaskManager | None = None,
) -> list[ToolDef]:
    """Create task-related tools sharing one background manager when enabled."""

    if provider is None:
        raise ValueError("provider is required")
    registry = _resolve_registry(profiles)
    runner = create_subagent_task_runner(
        provider=provider,
        workspace_root=workspace_root,
        profiles=registry,
        base_config=base_config,
        store_factory=store_factory,
        tool_runtime_factory=tool_runtime_factory,
        session_id_prefix=session_id_prefix,
    )
    description = create_agent_task_tool_description(
        registry,
        tool_permissions=(
            None if base_config is None else base_config.tool_permissions
        ),
    )
    manager = background_manager if allow_background else None
    if allow_background and manager is None:
        manager = BackgroundTaskManager()
    return [
        create_task_tool(
            runner,
            tool_id=tool_id,
            description=description,
            allow_background=allow_background,
            background_manager=manager,
        )
    ]


def _resolve_registry(
    profiles: AgentRegistry | Iterable[AgentProfile] | None,
) -> AgentRegistry:
    if profiles is None:
        return AgentRegistry([AgentProfile(name="general")], default_agent="general")
    if isinstance(profiles, AgentRegistry):
        return profiles
    return AgentRegistry(profiles, default_agent="general")


def create_agent_task_tool_description(
    registry: AgentRegistry,
    tool_permissions: Mapping[str, Any] | None = None,
) -> str:
    """Return provider-facing task tool text for visible subagent profiles."""

    profile_lines = [
        _agent_type_description_line(profile)
        for profile in registry.profiles()
        if _is_task_profile(profile)
        and _is_task_profile_permission_visible(profile, tool_permissions)
    ]
    lines = [
        DEFAULT_TASK_TOOL_DESCRIPTION,
        "",
        "Available agent types:",
    ]
    if profile_lines:
        lines.extend(profile_lines)
    else:
        lines.append("No subagents are available.")
    return "\n".join(lines)


def _agent_type_description_line(profile: AgentProfile) -> str:
    description = profile.description.strip() or MANUAL_SUBAGENT_DESCRIPTION
    return f"- {profile.name}: {description}"


def _is_task_profile(profile: AgentProfile) -> bool:
    if profile.metadata.get("hidden") is True:
        return False
    name = profile.name.strip().lower()
    if name in _PRIMARY_TASK_PROFILE_NAMES:
        return False
    mode = str(profile.metadata.get("mode") or "").strip().lower()
    return mode not in _PRIMARY_TASK_PROFILE_MODES


def _is_task_profile_permission_visible(
    profile: AgentProfile,
    tool_permissions: Mapping[str, Any] | None,
) -> bool:
    return is_permission_subject_visible(
        tool_permissions,
        tool_id="task",
        category="task",
        resource="subagent",
        subject=profile.name,
    )


def _child_config(
    *,
    profile: AgentProfile,
    base_config: RuntimeConfig | None,
    workspace_root: str | Path | None,
    metadata: Mapping[str, Any],
) -> RuntimeConfig:
    resolved_workspace_root = (
        workspace_root
        if workspace_root is not None
        else (base_config.workspace_root if base_config is not None else None)
    )
    max_iterations = (
        profile.max_iterations
        if profile.max_iterations is not None
        else (base_config.max_iterations if base_config is not None else None)
    )
    profile_permission_overlay = normalize_agent_permission_overlay(profile.metadata)
    base_tool_permissions = (
        {} if base_config is None else base_config.tool_permissions
    )
    base_metadata = dict(base_config.metadata) if base_config is not None else {}
    base_metadata.update(metadata)
    return RuntimeConfig(
        workspace_root=resolved_workspace_root,
        max_iterations=max_iterations,
        doom_loop_threshold=(
            3 if base_config is None else base_config.doom_loop_threshold
        ),
        max_context_parts=(
            base_config.max_context_parts if base_config is not None else None
        ),
        max_context_chars=(
            base_config.max_context_chars if base_config is not None else None
        ),
        max_context_tokens=(
            base_config.max_context_tokens if base_config is not None else None
        ),
        context_reserve_chars=(
            base_config.context_reserve_chars if base_config is not None else 0
        ),
        context_reserve_tokens=(
            base_config.context_reserve_tokens if base_config is not None else None
        ),
        default_provider_id=(
            "github-copilot"
            if base_config is None
            else base_config.default_provider_id
        ),
        default_model=(
            "gpt-5.4" if base_config is None else base_config.default_model
        ),
        compaction_auto=(
            True if base_config is None else base_config.compaction_auto
        ),
        compaction_rewrite_stored_history=(
            False
            if base_config is None
            else base_config.compaction_rewrite_stored_history
        ),
        compaction_prune=(
            True if base_config is None else base_config.compaction_prune
        ),
        compaction_tail_turns=(
            2 if base_config is None else base_config.compaction_tail_turns
        ),
        compaction_preserve_recent_chars=(
            None
            if base_config is None
            else base_config.compaction_preserve_recent_chars
        ),
        compaction_preserve_recent_tokens=(
            None
            if base_config is None
            else base_config.compaction_preserve_recent_tokens
        ),
        compaction_reserved_chars=(
            None if base_config is None else base_config.compaction_reserved_chars
        ),
        compaction_tool_output_max_chars=(
            2000
            if base_config is None
            else base_config.compaction_tool_output_max_chars
        ),
        enable_compaction_summarizer=(
            False
            if base_config is None
            else base_config.enable_compaction_summarizer
        ),
        provider_max_retries=(
            2 if base_config is None else base_config.provider_max_retries
        ),
        provider_retry_backoff_seconds=(
            0.0
            if base_config is None
            else base_config.provider_retry_backoff_seconds
        ),
        provider_retry_backoff_multiplier=(
            2.0
            if base_config is None
            else base_config.provider_retry_backoff_multiplier
        ),
        enable_context_overflow_retry=(
            True
            if base_config is None
            else base_config.enable_context_overflow_retry
        ),
        enable_session_revert_snapshots=(
            True
            if base_config is None
            else base_config.enable_session_revert_snapshots
        ),
        emit_llm_stream_events=(
            True
            if base_config is None
            else base_config.emit_llm_stream_events
        ),
        track_usage=(
            True if base_config is None else base_config.track_usage
        ),
        usage_pricing=(
            {} if base_config is None else dict(base_config.usage_pricing)
        ),
        enabled_tools=(
            None
            if base_config is None or base_config.enabled_tools is None
            else list(base_config.enabled_tools)
        ),
        disabled_tools=(
            [] if base_config is None else list(base_config.disabled_tools)
        ),
        model_aware_tool_selection=(
            True
            if base_config is None
            else base_config.model_aware_tool_selection
        ),
        tool_permissions=merge_tool_permission_configs(
            base_tool_permissions,
            profile_permission_overlay,
        ),
        runtime_mode=(
            "build" if base_config is None else base_config.runtime_mode
        ),
        enable_plan_tool=(
            None if base_config is None else base_config.enable_plan_tool
        ),
        plan_mode_read_only=(
            True if base_config is None else base_config.plan_mode_read_only
        ),
        enable_question_tool=(
            False if base_config is None else base_config.enable_question_tool
        ),
        enable_lsp_tool=(
            False if base_config is None else base_config.enable_lsp_tool
        ),
        inject_background_task_results=(
            True
            if base_config is None
            else base_config.inject_background_task_results
        ),
        structured_output_schema=(
            None
            if base_config is None or base_config.structured_output_schema is None
            else deepcopy(base_config.structured_output_schema)
        ),
        metadata=base_metadata,
        include_default_system_prompt=(
            False
            if base_config is None
            else base_config.include_default_system_prompt
        ),
        include_environment_context=(
            False
            if base_config is None
            else base_config.include_environment_context
        ),
        system_prompt_texts=(
            [] if base_config is None else list(base_config.system_prompt_texts)
        ),
        system_prompt_paths=(
            [] if base_config is None else list(base_config.system_prompt_paths)
        ),
        max_system_prompt_chars=(
            20000 if base_config is None else base_config.max_system_prompt_chars
        ),
        include_runtime_reminders=(
            False if base_config is None else base_config.include_runtime_reminders
        ),
        instruction_paths=(
            [] if base_config is None else list(base_config.instruction_paths)
        ),
        instruction_texts=(
            [] if base_config is None else list(base_config.instruction_texts)
        ),
        include_default_instructions=(
            True if base_config is None else base_config.include_default_instructions
        ),
        attach_read_instructions=(
            True if base_config is None else base_config.attach_read_instructions
        ),
        max_instruction_chars=(
            20000 if base_config is None else base_config.max_instruction_chars
        ),
        skill_directories=(
            [] if base_config is None else list(base_config.skill_directories)
        ),
        active_skills=list(profile.active_skills),
        include_skill_sidecar_content=(
            False
            if base_config is None
            else base_config.include_skill_sidecar_content
        ),
        max_skill_sidecar_chars=(
            4000 if base_config is None else base_config.max_skill_sidecar_chars
        ),
        command_directories=(
            [] if base_config is None else list(base_config.command_directories)
        ),
        enable_command_expansion=(
            True if base_config is None else base_config.enable_command_expansion
        ),
        max_command_chars=(
            20000 if base_config is None else base_config.max_command_chars
        ),
        resolve_prompt_references=(
            True if base_config is None else base_config.resolve_prompt_references
        ),
        max_prompt_reference_chars=(
            20000 if base_config is None else base_config.max_prompt_reference_chars
        ),
        max_prompt_directory_entries=(
            200 if base_config is None else base_config.max_prompt_directory_entries
        ),
        tool_output_max_lines=(
            2000 if base_config is None else base_config.tool_output_max_lines
        ),
        tool_output_max_bytes=(
            50 * 1024 if base_config is None else base_config.tool_output_max_bytes
        ),
        tool_output_truncation_direction=(
            "head"
            if base_config is None
            else base_config.tool_output_truncation_direction
        ),
        archive_truncated_tool_outputs=(
            True
            if base_config is None
            else base_config.archive_truncated_tool_outputs
        ),
        tool_output_dir=(
            None if base_config is None else base_config.tool_output_dir
        ),
    )


def _child_tool_overrides(
    profile: AgentProfile,
    *,
    available_tool_ids: Iterable[str] | None = None,
    parent_tool_permissions: Mapping[str, Any] | None = None,
) -> dict[str, bool] | None:
    overrides = dict(profile.tools or {})
    available = (
        None
        if available_tool_ids is None
        else {str(tool_id) for tool_id in available_tool_ids}
    )
    permission_overlay = normalize_agent_permission_overlay(profile.metadata)

    if not _permission_overlay_explicitly_permits(
        permission_overlay,
        _SUBAGENT_TASK_TOOL_IDS,
    ):
        for tool_id in _SUBAGENT_TASK_TOOL_IDS:
            _force_child_tool_disabled(overrides, tool_id, available)

    if not _permission_overlay_explicitly_permits(
        permission_overlay,
        _SUBAGENT_TODO_WRITE_TOOL_IDS,
    ):
        for tool_id in _SUBAGENT_TODO_WRITE_TOOL_IDS:
            _force_child_tool_disabled(overrides, tool_id, available)

    for tool_id in _parent_forced_disabled_tool_ids(parent_tool_permissions):
        _force_child_tool_disabled(overrides, tool_id, available)

    return overrides or None


def _force_child_tool_disabled(
    overrides: dict[str, bool],
    tool_id: str,
    available_tool_ids: set[str] | None,
) -> None:
    if available_tool_ids is not None and tool_id not in available_tool_ids:
        if tool_id not in overrides:
            return
    overrides[tool_id] = False


def _permission_overlay_explicitly_permits(
    permission_overlay: Mapping[str, Any],
    permission_keys: Iterable[str],
) -> bool:
    for permission_key in permission_keys:
        if permission_key not in permission_overlay:
            continue
        if _permission_rule_explicitly_permits(permission_overlay[permission_key]):
            return True
    return False


def _permission_rule_explicitly_permits(rule: Any) -> bool:
    if isinstance(rule, str):
        return rule in _SUBAGENT_GUARD_ALLOW_ACTIONS
    if not isinstance(rule, Mapping):
        return False

    action = rule.get("action")
    if isinstance(action, str):
        return action in _SUBAGENT_GUARD_ALLOW_ACTIONS

    for nested_rule in rule.values():
        if isinstance(nested_rule, str):
            if nested_rule in _SUBAGENT_GUARD_ALLOW_ACTIONS:
                return True
            continue
        if isinstance(nested_rule, Mapping):
            nested_action = nested_rule.get("action")
            if (
                isinstance(nested_action, str)
                and nested_action in _SUBAGENT_GUARD_ALLOW_ACTIONS
            ):
                return True
    return False


def _parent_forced_disabled_tool_ids(
    tool_permissions: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(tool_permissions, Mapping):
        return []
    disabled: set[str] = set()
    for raw_key, rule in tool_permissions.items():
        key = str(raw_key)
        if _permission_rule_action(rule) != "deny":
            continue
        aliases = _MUTATING_FILE_PERMISSION_ALIASES.get(key)
        if aliases is not None:
            disabled.update(aliases)
        elif key in {"write", "edit", "apply_patch"}:
            disabled.add(key)
    return sorted(disabled)


def _permission_rule_action(rule: Any) -> str | None:
    if isinstance(rule, str):
        return rule
    if isinstance(rule, Mapping):
        action = rule.get("action")
        if isinstance(action, str):
            return action
    return None


def _make_store(store_factory: Callable[[], SessionStore] | None) -> SessionStore | None:
    if store_factory is None:
        return InMemorySessionStore()
    return store_factory()


def _make_tool_runtime(
    tool_runtime_factory: Callable[[AgentProfile], ToolRuntime] | None,
    profile: AgentProfile,
) -> ToolRuntime | None:
    if tool_runtime_factory is None:
        return None
    return tool_runtime_factory(profile)


def _child_prompt(profile: AgentProfile, request: TaskToolRequest) -> str:
    parts: list[str] = []
    if profile.prompt:
        parts.extend(
            [
                f'<agent_instructions name="{escape(profile.name, quote=True)}">',
                f"prompt: {json.dumps(profile.prompt, sort_keys=True)}",
                "</agent_instructions>",
                "",
            ]
        )
    task_attrs = [f'description="{escape(request.description, quote=True)}"']
    if request.command:
        task_attrs.append(f'command="{escape(request.command, quote=True)}"')
    parts.extend(
        [
            f"<task_prompt {' '.join(task_attrs)}>",
            f"prompt: {json.dumps(request.prompt, sort_keys=True)}",
            "</task_prompt>",
        ]
    )
    return "\n".join(parts)


def _child_metadata(
    *,
    request: TaskToolRequest,
    profile: AgentProfile,
    child_session_id: str,
) -> dict[str, Any]:
    metadata = {
        "parent_session_id": request.session_id,
        "task_id": request.task_id,
        "subagent_type": request.subagent_type,
        "agent_profile": profile.name,
        "profile_name": profile.name,
        "profile": profile.name,
        "child_session_id": child_session_id,
    }
    if profile.metadata:
        metadata["agent_profile_metadata"] = dict(profile.metadata)
    permission_overlay = normalize_agent_permission_overlay(profile.metadata)
    if permission_overlay:
        metadata[AGENT_PERMISSION_OVERLAY_METADATA_KEY] = permission_overlay
        metadata[AGENT_PERMISSION_OVERLAY_SOURCE_KEY] = AGENT_PERMISSION_OVERLAY_SOURCE
    if request.metadata:
        metadata["parent_task_metadata"] = dict(request.metadata)
    return metadata


def _child_session_id(
    *,
    prefix: str,
    parent_session_id: str | None,
    task_id: str,
) -> str:
    parts = [_safe_id_part(prefix) or "subagent"]
    if parent_session_id:
        parts.append(_safe_id_part(parent_session_id))
    parts.append(_safe_id_part(task_id) or "task")
    return "-".join(part for part in parts if part)


def _safe_id_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip()).strip("_")


def _assistant_text(message: Message | None) -> str:
    if message is None:
        return ""
    text_parts: list[str] = []
    for part in message.parts:
        if part.type is MessagePartType.TEXT and part.text:
            text_parts.append(part.text)
    return "\n".join(text_parts)


def _assistant_error_text(message: Message | None) -> str:
    if message is None:
        return ""
    error_parts: list[str] = []
    for part in message.parts:
        if part.type is MessagePartType.ERROR and part.text:
            error_parts.append(part.text)
    return "\n".join(error_parts)


def _runtime_error_text(events: list[Any]) -> str:
    for event in reversed(events):
        if getattr(event, "type", None) != "error":
            continue
        message = getattr(event, "message", "")
        if message:
            return str(message)
        payload = getattr(event, "payload", {})
        if isinstance(payload, Mapping) and payload.get("error"):
            return str(payload["error"])
    return ""


def _non_completed_text(result: SubagentRunResult) -> str:
    if result.text and result.text != EMPTY_SUBAGENT_RESULT_MESSAGE:
        return result.text
    return f"Subagent run did not complete: {result.status}"


def _exception_text(exc: Exception) -> str:
    if exc.args and isinstance(exc.args[0], str):
        return exc.args[0]
    return str(exc) or exc.__class__.__name__


__all__ = [
    "BackgroundTaskManager",
    "EMPTY_SUBAGENT_RESULT_MESSAGE",
    "SubagentRunResult",
    "create_agent_task_tool",
    "create_agent_task_tools",
    "create_subagent_task_runner",
]
