"""High-level Runtime v2 facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import json
from pathlib import Path
from typing import Any, Union

from ..compaction.controller import CompactionSummarizer
from ..event_bus import RuntimeEventBus
from ..instructions import InstructionContextBuilder, ReadInstructionResolver
from ..llm.adapter import LLMEventAdapter
from ..loop.provider import LLMProvider
from ..loop.runner import LoopStatus, ProviderCallable, RuntimeLoopResult, RuntimeLoopRunner
from ..lsp import LSPClient
from ..permissions import ConfiguredPermissionBroker, PermissionEvaluator
from ..prompt import resolve_prompt_references
from ..questions import QuestionBroker
from ..session.protocol import SessionStore
from ..session.checkpoint import SessionCheckpoint
from ..session.models import Session
from ..session.store import InMemorySessionStore
from ..skills.commands import SkillCommandResult, parse_skill_commands
from ..skills.context import SkillContextBuilder
from ..skills.discovery import SkillDiscovery
from ..skills.tool import build_skill_list_tool, build_skill_tool
from ..system_prompt import SystemPromptBuilder
from ..tools.builtin import (
    create_core_tool_registry,
    create_plan_exit_tool,
    create_question_tool,
)
from ..tools.definition import OutputPolicy
from ..tools.external import ExternalToolProvider, register_external_tools
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime
from ..tools.selection import ToolSelection
from ..tools.truncation import ToolOutputTruncator, TruncationLimits
from .config import RuntimeConfig
from .run_state import RuntimeRunState


PLAN_MODE_MUTATING_TOOLS = {
    "apply_patch",
    "edit",
    "write_file",
    "shell_exec",
    "shell_kill",
    "task_cancel",
}


class AgentRuntime:
    """Convenience facade that wires store, tools, renderer, and loop runner."""

    def __init__(
        self,
        *,
        provider: Union[LLMProvider, ProviderCallable],
        config: RuntimeConfig | None = None,
        workspace_root: str | Path | None = None,
        max_iterations: int | None = None,
        max_context_parts: int | None = None,
        max_context_chars: int | None = None,
        context_reserve_chars: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        store: SessionStore | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
        external_tool_providers: Iterable[ExternalToolProvider] | None = None,
        external_tools_allow_override: bool = False,
        permission_evaluator: PermissionEvaluator | None = None,
        adapter: LLMEventAdapter | None = None,
        skill_discovery: SkillDiscovery | None = None,
        skill_context_builder: SkillContextBuilder | None = None,
        instruction_context_builder: InstructionContextBuilder | None = None,
        system_prompt_builder: SystemPromptBuilder | None = None,
        event_bus: RuntimeEventBus | None = None,
        run_state: RuntimeRunState | None = None,
        compaction_summarizer: CompactionSummarizer | None = None,
        question_broker: QuestionBroker | None = None,
        lsp_client: LSPClient | None = None,
    ) -> None:
        self.config = _resolve_config(
            config,
            workspace_root=workspace_root,
            max_iterations=max_iterations,
            max_context_parts=max_context_parts,
            max_context_chars=max_context_chars,
            context_reserve_chars=context_reserve_chars,
            metadata=metadata,
        )
        self.provider = provider
        self.adapter = adapter
        self.compaction_summarizer = compaction_summarizer
        self.question_broker = question_broker or QuestionBroker()
        self.store = store or InMemorySessionStore()
        self.skill_discovery = _resolve_skill_discovery(
            config=self.config,
            skill_discovery=skill_discovery,
        )
        self.tool_runtime = _resolve_tool_runtime(
            workspace_root=self.config.workspace_root,
            config=self.config,
            tool_registry=tool_registry,
            tool_runtime=tool_runtime,
            external_tool_providers=external_tool_providers,
            external_tools_allow_override=external_tools_allow_override,
            permission_evaluator=permission_evaluator,
            skill_discovery=self.skill_discovery,
            question_broker=self.question_broker,
            lsp_client=lsp_client,
        )
        self.skill_context_builder = _resolve_skill_context_builder(
            config=self.config,
            skill_discovery=self.skill_discovery,
            skill_context_builder=skill_context_builder,
        )
        self.instruction_context_builder = _resolve_instruction_context_builder(
            config=self.config,
            instruction_context_builder=instruction_context_builder,
        )
        self.system_prompt_builder = _resolve_system_prompt_builder(
            config=self.config,
            system_prompt_builder=system_prompt_builder,
        )
        self.active_skills = _unique_skill_names(self.config.active_skills)
        self.event_bus = event_bus or RuntimeEventBus()
        self.run_state = run_state or RuntimeRunState()

    async def run(
        self,
        user_text: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
    ) -> RuntimeLoopResult:
        skill_command = parse_skill_commands(user_text)
        active_skills = _apply_skill_command(self.active_skills, skill_command)
        resolved_session_id = session_id or self.store.create_session().session_id
        run_id = self.run_state.begin(resolved_session_id)
        try:
            run_metadata = self._base_run_metadata(metadata)
            run_metadata["run_id"] = run_id
            self._annotate_skill_metadata(run_metadata, active_skills)
            run_metadata["skill_command"] = {
                "add": list(skill_command.add),
                "clear": skill_command.clear,
                "cleaned_text": skill_command.cleaned_text,
            }
            system_prompt_messages = self._build_system_prompt_messages(run_metadata)
            instruction_context_messages = self._build_instruction_context_messages()
            skill_context_messages = self._build_skill_context_messages(active_skills)
            context_messages = [
                *system_prompt_messages,
                *instruction_context_messages,
                *skill_context_messages,
            ]
            self.active_skills = active_skills

            run_metadata["system_prompt_context_count"] = len(system_prompt_messages)
            run_metadata["instruction_context_count"] = len(instruction_context_messages)
            run_metadata["skill_context_count"] = len(skill_context_messages)
            user_parts = self._resolve_user_parts(skill_command.cleaned_text)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=self.tool_runtime,
                max_iterations=self.config.max_iterations,
                doom_loop_threshold=self.config.doom_loop_threshold,
                max_context_parts=self.config.max_context_parts,
                max_context_chars=self.config.max_context_chars,
                context_reserve_chars=self.config.context_reserve_chars,
                provider_max_retries=self.config.provider_max_retries,
                provider_retry_backoff_seconds=(
                    self.config.provider_retry_backoff_seconds
                ),
                provider_retry_backoff_multiplier=(
                    self.config.provider_retry_backoff_multiplier
                ),
                enable_context_overflow_retry=(
                    self.config.enable_context_overflow_retry
                ),
                emit_llm_stream_events=self.config.emit_llm_stream_events,
                track_usage=self.config.track_usage,
                usage_pricing=self.config.usage_pricing,
                event_bus=self.event_bus,
                is_cancelled=lambda: self.run_state.is_cancelled(resolved_session_id),
                tool_selection=_config_tool_selection(self.config),
                compaction_summarizer=(
                    self.compaction_summarizer
                    if self.config.enable_compaction_summarizer
                    else None
                ),
            )
            result = await runner.run(
                user_text=skill_command.cleaned_text,
                user_parts=user_parts,
                session_id=resolved_session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                tools=tools,
            )
        except asyncio.CancelledError:
            self.run_state.finish(resolved_session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self.run_state.finish(resolved_session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(resolved_session_id, result.status)
        return result

    async def resume(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
    ) -> RuntimeLoopResult:
        self.store.get_session(session_id)
        run_id = self.run_state.begin(session_id)
        try:
            run_metadata = self._base_run_metadata(metadata)
            run_metadata["run_id"] = run_id
            self._annotate_skill_metadata(run_metadata, self.active_skills)
            run_metadata["resume"] = True
            system_prompt_messages = self._build_system_prompt_messages(run_metadata)
            instruction_context_messages = self._build_instruction_context_messages()
            skill_context_messages = self._build_skill_context_messages(self.active_skills)
            context_messages = [
                *system_prompt_messages,
                *instruction_context_messages,
                *skill_context_messages,
            ]
            run_metadata["system_prompt_context_count"] = len(system_prompt_messages)
            run_metadata["instruction_context_count"] = len(instruction_context_messages)
            run_metadata["skill_context_count"] = len(skill_context_messages)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=self.tool_runtime,
                max_iterations=self.config.max_iterations,
                doom_loop_threshold=self.config.doom_loop_threshold,
                max_context_parts=self.config.max_context_parts,
                max_context_chars=self.config.max_context_chars,
                context_reserve_chars=self.config.context_reserve_chars,
                provider_max_retries=self.config.provider_max_retries,
                provider_retry_backoff_seconds=(
                    self.config.provider_retry_backoff_seconds
                ),
                provider_retry_backoff_multiplier=(
                    self.config.provider_retry_backoff_multiplier
                ),
                enable_context_overflow_retry=(
                    self.config.enable_context_overflow_retry
                ),
                emit_llm_stream_events=self.config.emit_llm_stream_events,
                track_usage=self.config.track_usage,
                usage_pricing=self.config.usage_pricing,
                event_bus=self.event_bus,
                is_cancelled=lambda: self.run_state.is_cancelled(session_id),
                tool_selection=_config_tool_selection(self.config),
                compaction_summarizer=(
                    self.compaction_summarizer
                    if self.config.enable_compaction_summarizer
                    else None
                ),
            )
            result = await runner.run(
                user_text="",
                session_id=session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                append_user_message=False,
                tools=tools,
            )
        except asyncio.CancelledError:
            self.run_state.finish(session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self.run_state.finish(session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(session_id, result.status)
        return result

    def approve_permission(self, request_id: str, *, always: bool = False):
        approve = getattr(self.tool_runtime.permission_evaluator, "approve", None)
        if not callable(approve):
            raise TypeError("permission evaluator does not support approve")
        return approve(request_id, always=always)

    def deny_permission(
        self,
        request_id: str,
        *,
        always: bool = False,
        reason: str | None = None,
    ):
        deny = getattr(self.tool_runtime.permission_evaluator, "deny", None)
        if not callable(deny):
            raise TypeError("permission evaluator does not support deny")
        return deny(request_id, always=always, reason=reason)

    def pending_permissions(self) -> list[dict[str, Any]]:
        pending = getattr(self.tool_runtime.permission_evaluator, "pending", None)
        if not callable(pending):
            raise TypeError("permission evaluator does not support pending")
        return [_permission_request_to_dict(request) for request in pending()]

    def pending_permission_requests(self) -> list[dict[str, Any]]:
        return self.pending_permissions()

    def answer_question(self, request_id: str, answers):
        return self.question_broker.answer(request_id, answers)

    def pending_questions(self) -> list[dict[str, Any]]:
        return [
            _question_request_to_dict(request)
            for request in self.question_broker.pending()
        ]

    def drain_background_tasks(
        self,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        manager = _background_task_manager(self.tool_runtime)
        if manager is None:
            return []
        return [
            _background_task_record_payload(manager, record)
            for record in manager.drain_completed(session_id=session_id)
        ]

    def cancel(self, session_id: str) -> bool:
        return self.run_state.cancel(session_id)

    def create_checkpoint(
        self,
        session_id: str,
        *,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> SessionCheckpoint:
        create_checkpoint = self._checkpoint_store_method("create_checkpoint")
        return create_checkpoint(
            session_id,
            label=label,
            metadata=metadata,
            message_id=message_id,
        )

    def list_checkpoints(self, session_id: str) -> list[SessionCheckpoint]:
        list_checkpoints = self._checkpoint_store_method("list_checkpoints")
        return list_checkpoints(session_id)

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Session:
        restore_checkpoint = self._checkpoint_store_method("restore_checkpoint")
        return restore_checkpoint(session_id, checkpoint_id)

    def delete_checkpoint(self, session_id: str, checkpoint_id: str) -> bool:
        delete_checkpoint = self._checkpoint_store_method("delete_checkpoint")
        return delete_checkpoint(session_id, checkpoint_id)

    def _build_instruction_context_messages(self):
        return self.instruction_context_builder.build_messages()

    def _build_system_prompt_messages(self, metadata: Mapping[str, Any]):
        return self.system_prompt_builder.build_messages(metadata=metadata)

    def _build_skill_context_messages(self, active_skills: list[str]):
        if not active_skills:
            return []
        if self.skill_context_builder is None:
            raise ValueError(
                "Active skills require skill_context_builder, skill_discovery, "
                "or config.skill_directories"
            )
        return self.skill_context_builder.build_messages(active_skills)

    def _resolve_user_parts(self, user_text: str):
        if (
            not self.config.resolve_prompt_references
            or self.config.workspace_root is None
            or not user_text
        ):
            return None
        resolved_prompt = resolve_prompt_references(
            user_text,
            workspace_root=self.config.workspace_root,
            max_file_chars=self.config.max_prompt_reference_chars,
            max_directory_entries=self.config.max_prompt_directory_entries,
        )
        return resolved_prompt.parts

    def _base_run_metadata(self, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        run_metadata = dict(self.config.metadata)
        run_metadata.update(metadata or {})
        run_metadata["max_iterations"] = self.config.max_iterations
        run_metadata["runtime_mode"] = self.config.runtime_mode
        run_metadata["plan_mode_read_only"] = self.config.plan_mode_read_only
        run_metadata["enable_question_tool"] = self.config.enable_question_tool
        run_metadata["emit_llm_stream_events"] = self.config.emit_llm_stream_events
        if self.config.workspace_root is not None:
            run_metadata["workspace_root"] = str(self.config.workspace_root)
        run_metadata["tool_output_truncation_enabled"] = (
            self.config.workspace_root is not None
        )
        return run_metadata

    def _annotate_skill_metadata(
        self,
        run_metadata: dict[str, Any],
        active_skills: Iterable[str],
    ) -> None:
        active = list(active_skills)
        run_metadata["active_skills"] = active
        run_metadata["active_skill_count"] = len(active)
        if self.skill_discovery is not None:
            run_metadata["available_skill_count"] = len(self.skill_discovery.discover())

    def _checkpoint_store_method(self, name: str):
        method = getattr(self.store, name, None)
        if not callable(method):
            raise TypeError("session store does not support checkpoints")
        return method


def _resolve_config(
    config: RuntimeConfig | None,
    *,
    workspace_root: str | Path | None,
    max_iterations: int | None,
    max_context_parts: int | None,
    max_context_chars: int | None,
    context_reserve_chars: int | None,
    metadata: Mapping[str, Any] | None,
) -> RuntimeConfig:
    if config is None:
        return RuntimeConfig(
            workspace_root=workspace_root,
            max_iterations=max_iterations if max_iterations is not None else 4,
            doom_loop_threshold=3,
            max_context_parts=max_context_parts,
            max_context_chars=max_context_chars,
            context_reserve_chars=(
                context_reserve_chars if context_reserve_chars is not None else 0
            ),
            metadata=dict(metadata or {}),
        )

    resolved_metadata = dict(config.metadata)
    resolved_metadata.update(metadata or {})
    return RuntimeConfig(
        workspace_root=workspace_root if workspace_root is not None else config.workspace_root,
        max_iterations=max_iterations if max_iterations is not None else config.max_iterations,
        doom_loop_threshold=config.doom_loop_threshold,
        max_context_parts=(
            max_context_parts if max_context_parts is not None else config.max_context_parts
        ),
        max_context_chars=(
            max_context_chars if max_context_chars is not None else config.max_context_chars
        ),
        context_reserve_chars=(
            context_reserve_chars
            if context_reserve_chars is not None
            else config.context_reserve_chars
        ),
        enable_compaction_summarizer=config.enable_compaction_summarizer,
        provider_max_retries=config.provider_max_retries,
        provider_retry_backoff_seconds=config.provider_retry_backoff_seconds,
        provider_retry_backoff_multiplier=config.provider_retry_backoff_multiplier,
        enable_context_overflow_retry=config.enable_context_overflow_retry,
        emit_llm_stream_events=config.emit_llm_stream_events,
        track_usage=config.track_usage,
        usage_pricing=dict(config.usage_pricing),
        enabled_tools=(
            None if config.enabled_tools is None else list(config.enabled_tools)
        ),
        disabled_tools=list(config.disabled_tools),
        tool_permissions=dict(config.tool_permissions),
        runtime_mode=config.runtime_mode,
        enable_plan_tool=config.enable_plan_tool,
        plan_mode_read_only=config.plan_mode_read_only,
        enable_question_tool=config.enable_question_tool,
        enable_lsp_tool=config.enable_lsp_tool,
        enable_background_shell=config.enable_background_shell,
        background_shell_max_buffer_bytes=config.background_shell_max_buffer_bytes,
        metadata=resolved_metadata,
        include_default_system_prompt=config.include_default_system_prompt,
        system_prompt_texts=list(config.system_prompt_texts),
        system_prompt_paths=list(config.system_prompt_paths),
        max_system_prompt_chars=config.max_system_prompt_chars,
        include_runtime_reminders=config.include_runtime_reminders,
        instruction_paths=list(config.instruction_paths),
        instruction_texts=list(config.instruction_texts),
        include_default_instructions=config.include_default_instructions,
        attach_read_instructions=config.attach_read_instructions,
        max_instruction_chars=config.max_instruction_chars,
        skill_directories=list(config.skill_directories),
        active_skills=list(config.active_skills),
        enable_skill_list_tool=config.enable_skill_list_tool,
        include_skill_sidecar_content=config.include_skill_sidecar_content,
        max_skill_sidecar_chars=config.max_skill_sidecar_chars,
        resolve_prompt_references=config.resolve_prompt_references,
        max_prompt_reference_chars=config.max_prompt_reference_chars,
        max_prompt_directory_entries=config.max_prompt_directory_entries,
        tool_output_max_lines=config.tool_output_max_lines,
        tool_output_max_bytes=config.tool_output_max_bytes,
        tool_output_truncation_direction=config.tool_output_truncation_direction,
        archive_truncated_tool_outputs=config.archive_truncated_tool_outputs,
        tool_output_dir=config.tool_output_dir,
    )


def _resolve_tool_runtime(
    *,
    workspace_root: str | Path | None,
    config: RuntimeConfig,
    tool_registry: ToolRegistry | None,
    tool_runtime: ToolRuntime | None,
    external_tool_providers: Iterable[ExternalToolProvider] | None,
    external_tools_allow_override: bool,
    permission_evaluator: PermissionEvaluator | None,
    skill_discovery: SkillDiscovery | None,
    question_broker: QuestionBroker,
    lsp_client: LSPClient | None,
) -> ToolRuntime:
    if tool_runtime is not None:
        if tool_registry is not None and tool_registry is not tool_runtime.registry:
            raise ValueError(
                "tool_registry must match tool_runtime.registry when both are provided"
            )
        return tool_runtime

    registry = tool_registry
    if registry is None:
        if workspace_root is not None:
            instruction_resolver = (
                ReadInstructionResolver(
                    workspace_root,
                    max_instruction_chars=config.max_instruction_chars,
                )
                if config.attach_read_instructions
                else None
            )
            registry = create_core_tool_registry(
                workspace_root,
                skill_discovery=skill_discovery,
                include_skill_list_tool=config.enable_skill_list_tool,
                max_skill_sidecar_chars=config.max_skill_sidecar_chars,
                question_broker=question_broker,
                include_question_tool=config.enable_question_tool,
                instruction_resolver=instruction_resolver,
                lsp_client=lsp_client,
                include_lsp_tool=config.enable_lsp_tool,
                include_plan_tool=_plan_tool_enabled(config),
                enable_background_shell=config.enable_background_shell,
                background_shell_max_buffer_bytes=(
                    config.background_shell_max_buffer_bytes
                ),
            )
        else:
            if config.enable_lsp_tool or lsp_client is not None:
                raise ValueError("workspace_root is required to enable the lsp tool")
            registry = ToolRegistry()
            if _plan_tool_enabled(config):
                registry.register(create_plan_exit_tool())
            if config.enable_question_tool:
                registry.register(create_question_tool(question_broker))
            if skill_discovery is not None:
                registry.register(
                    build_skill_tool(
                        skill_discovery,
                        max_sidecar_chars=config.max_skill_sidecar_chars,
                    )
                )
            if _skill_list_tool_enabled(config, skill_discovery=skill_discovery):
                registry.register(
                    build_skill_list_tool(skill_discovery or SkillDiscovery([]))
                )
    if external_tool_providers is not None:
        register_external_tools(
            registry,
            external_tool_providers,
            allow_override=external_tools_allow_override,
        )
    resolved_permission_evaluator = permission_evaluator
    if resolved_permission_evaluator is None and config.tool_permissions:
        resolved_permission_evaluator = ConfiguredPermissionBroker(
            config.tool_permissions
        )

    return ToolRuntime(
        registry,
        permission_evaluator=resolved_permission_evaluator,
        default_output_policy=_tool_output_policy(config),
        output_truncator=_resolve_tool_output_truncator(
            workspace_root=workspace_root,
            config=config,
        ),
    )


def _tool_output_policy(config: RuntimeConfig) -> OutputPolicy:
    return OutputPolicy(
        max_lines=config.tool_output_max_lines,
        max_bytes=config.tool_output_max_bytes,
        truncation_direction=config.tool_output_truncation_direction,
        archive_full_output=config.archive_truncated_tool_outputs,
    )


def _resolve_tool_output_truncator(
    *,
    workspace_root: str | Path | None,
    config: RuntimeConfig,
) -> ToolOutputTruncator | None:
    if workspace_root is None:
        return None

    root = Path(workspace_root).expanduser().resolve()
    if config.tool_output_dir is None:
        output_dir = root / ".efp_runtime" / "tool-output"
    else:
        output_dir = Path(config.tool_output_dir).expanduser()
        if not output_dir.is_absolute():
            output_dir = root / output_dir

    return ToolOutputTruncator(
        output_dir,
        limits=TruncationLimits(
            max_lines=config.tool_output_max_lines,
            max_bytes=config.tool_output_max_bytes,
            direction=config.tool_output_truncation_direction,
        ),
        archive_full_output=config.archive_truncated_tool_outputs,
    )


def _resolve_skill_discovery(
    *,
    config: RuntimeConfig,
    skill_discovery: SkillDiscovery | None,
) -> SkillDiscovery | None:
    if skill_discovery is not None:
        return skill_discovery
    if config.skill_directories:
        return SkillDiscovery(config.skill_directories)
    return None


def _resolve_skill_context_builder(
    *,
    config: RuntimeConfig,
    skill_discovery: SkillDiscovery | None,
    skill_context_builder: SkillContextBuilder | None,
) -> SkillContextBuilder | None:
    if skill_context_builder is not None:
        return skill_context_builder

    discovery = skill_discovery
    if discovery is None and config.skill_directories:
        discovery = SkillDiscovery(config.skill_directories)
    if discovery is None:
        return None

    return SkillContextBuilder(
        discovery,
        include_sidecar_content=config.include_skill_sidecar_content,
        max_sidecar_chars=config.max_skill_sidecar_chars,
    )


def _resolve_instruction_context_builder(
    *,
    config: RuntimeConfig,
    instruction_context_builder: InstructionContextBuilder | None,
) -> InstructionContextBuilder:
    if instruction_context_builder is not None:
        return instruction_context_builder

    return InstructionContextBuilder(
        workspace_root=config.workspace_root,
        instruction_paths=config.instruction_paths,
        instruction_texts=config.instruction_texts,
        include_default_files=config.include_default_instructions,
        max_instruction_chars=config.max_instruction_chars,
    )


def _resolve_system_prompt_builder(
    *,
    config: RuntimeConfig,
    system_prompt_builder: SystemPromptBuilder | None,
) -> SystemPromptBuilder:
    if system_prompt_builder is not None:
        return system_prompt_builder

    return SystemPromptBuilder(
        workspace_root=config.workspace_root,
        include_default_system_prompt=config.include_default_system_prompt,
        system_prompt_texts=config.system_prompt_texts,
        system_prompt_paths=config.system_prompt_paths,
        max_system_prompt_chars=config.max_system_prompt_chars,
        include_runtime_reminders=config.include_runtime_reminders,
    )


def _apply_skill_command(
    active_skills: list[str],
    command: SkillCommandResult,
) -> list[str]:
    updated = [] if command.clear else list(active_skills)
    for name in command.add:
        normalized = str(name).strip()
        if normalized and normalized not in updated:
            updated.append(normalized)
    return updated


def _unique_skill_names(names: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for name in names:
        normalized = str(name).strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _config_tool_selection(config: RuntimeConfig) -> ToolSelection:
    forced_disabled = (
        set(PLAN_MODE_MUTATING_TOOLS)
        if config.runtime_mode == "plan" and config.plan_mode_read_only
        else set()
    )
    return ToolSelection(
        enabled=None if config.enabled_tools is None else set(config.enabled_tools),
        disabled=set(config.disabled_tools),
        forced_disabled=forced_disabled,
    )


def _plan_tool_enabled(config: RuntimeConfig) -> bool:
    if config.enable_plan_tool is None:
        return config.runtime_mode == "plan"
    return bool(config.enable_plan_tool)


def _skill_list_tool_enabled(
    config: RuntimeConfig,
    *,
    skill_discovery: SkillDiscovery | None,
) -> bool:
    if config.enable_skill_list_tool is not None:
        return bool(config.enable_skill_list_tool)
    return skill_discovery is not None or bool(config.skill_directories)


def _background_task_manager(tool_runtime: ToolRuntime) -> Any:
    for tool in tool_runtime.registry.list():
        runtime_metadata = getattr(tool, "runtime_metadata", {}) or {}
        if not isinstance(runtime_metadata, Mapping):
            continue
        manager = runtime_metadata.get("background_task_manager")
        if manager is not None and callable(getattr(manager, "drain_completed", None)):
            return manager
    return None


def _background_task_record_payload(manager: Any, record: Any) -> dict[str, Any]:
    converter = getattr(manager, "record_to_dict", None)
    if callable(converter):
        return converter(record)
    result = getattr(record, "result", None)
    result_payload = None
    if result is not None:
        result_payload = {
            "task_id": getattr(result, "task_id", None),
            "text": getattr(result, "text", ""),
            "state": getattr(result, "state", ""),
            "metadata": dict(getattr(result, "metadata", {}) or {}),
        }
    payload = {
        "task_id": getattr(record, "task_id", None),
        "description": getattr(record, "description", ""),
        "prompt": getattr(record, "prompt", ""),
        "subagent_type": getattr(record, "subagent_type", ""),
        "session_id": getattr(record, "session_id", None),
        "started_at": getattr(record, "started_at", None),
        "finished_at": getattr(record, "finished_at", None),
        "state": getattr(record, "state", ""),
        "background": True,
        "result": result_payload,
        "error": getattr(record, "error", None),
        "metadata": dict(getattr(record, "metadata", {}) or {}),
    }
    if result_payload is not None:
        payload["text"] = result_payload["text"]
        payload["result_metadata"] = dict(result_payload["metadata"])
    return payload


def _permission_request_to_dict(request: Any) -> dict[str, Any]:
    if hasattr(request, "to_dict"):
        payload = request.to_dict()
    elif isinstance(request, Mapping):
        payload = dict(request)
    else:
        raise TypeError("pending permission request must be mapping-like")
    encoded = json.dumps(payload, sort_keys=True, default=str)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("pending permission request must encode to a JSON object")
    return decoded


def _question_request_to_dict(request: Any) -> dict[str, Any]:
    if hasattr(request, "to_dict"):
        payload = request.to_dict()
    elif isinstance(request, Mapping):
        payload = dict(request)
    else:
        raise TypeError("pending question request must be mapping-like")
    encoded = json.dumps(payload, sort_keys=True, default=str)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("pending question request must encode to a JSON object")
    return decoded


__all__ = ["AgentRuntime"]
