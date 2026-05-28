"""High-level Runtime v2 facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from ..compaction.controller import CompactionController, CompactionSummarizer
from ..compaction.strategy import (
    BudgetCompactionStrategy,
    CompactionResult,
    ContextBudget,
)
from ..commands import CommandExpansionResult, CommandRegistry, expand_command
from ..event_bus import RuntimeEventBus
from ..events import RuntimeEvent
from ..instructions import InstructionContextBuilder, ReadInstructionResolver
from ..llm.adapter import LLMEventAdapter
from ..loop.provider import LLMProvider
from ..loop.runner import LoopStatus, ProviderCallable, RuntimeLoopResult, RuntimeLoopRunner
from ..lsp import LSPClient
from ..permissions import (
    AGENT_PERMISSION_OVERLAY_METADATA_KEY,
    AGENT_PERMISSION_OVERLAY_SOURCE,
    AGENT_PERMISSION_OVERLAY_SOURCE_KEY,
    ConfiguredPermissionBroker,
    PermissionConfig,
    PermissionEvaluator,
    is_permission_subject_hidden,
    normalize_agent_permission_overlay,
)
from ..prompt import resolve_prompt_references
from ..questions import QuestionBroker
from ..session.protocol import SessionStore
from ..session.checkpoint import SessionCheckpoint
from ..session.models import Message, MessagePart, MessagePartType, MessageRole, Session
from ..session.store import InMemorySessionStore
from ..skills.commands import SkillCommandResult, parse_skill_commands
from ..skills.context import SkillContextBuilder
from ..skills.discovery import SkillDiscovery
from ..skills.tool import build_skill_list_tool, build_skill_tool
from ..system_prompt import SystemPromptBuilder
from ..tools.builtin import (
    DEFAULT_STRUCTURED_OUTPUT_TOOL_ID,
    create_core_tool_registry,
    create_plan_exit_tool,
    create_question_tool,
    create_structured_output_tool,
)
from ..tools.builtin.task import format_background_task_notification
from ..tools.definition import OutputPolicy
from ..tools.external import ExternalToolProvider, register_external_tools
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime
from ..tools.selection import ToolSelection
from ..tools.truncation import ToolOutputTruncator, TruncationLimits
from .config import RuntimeConfig
from .run_state import RuntimeRunState

if TYPE_CHECKING:
    from ..agents.profile import AgentProfile
    from ..agents.registry import AgentRegistry


PLAN_MODE_MUTATING_TOOLS = {
    "apply_patch",
    "bash",
    "edit",
    "shell_exec",
    "shell_kill",
    "task",
    "task_cancel",
    "write",
    "write_file",
}

STRUCTURED_OUTPUT_SYSTEM_PROMPT = (
    "IMPORTANT: The user has requested structured output. You MUST use the "
    "StructuredOutput tool to provide your final response. Do NOT respond with "
    "plain text."
)


@dataclass(frozen=True)
class _AgentMentionCandidate:
    name: str
    stripped_text: str


@dataclass(frozen=True)
class _AgentMention:
    name: str
    profile: Any
    stripped_text: str


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
        command_registry: CommandRegistry | None = None,
        event_bus: RuntimeEventBus | None = None,
        run_state: RuntimeRunState | None = None,
        compaction_summarizer: CompactionSummarizer | None = None,
        question_broker: QuestionBroker | None = None,
        lsp_client: LSPClient | None = None,
        agent_registry: "AgentRegistry | None" = None,
        default_agent: str | None = None,
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
        self.command_registry = _resolve_command_registry(
            self.config,
            command_registry=command_registry,
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
        self.agent_registry = agent_registry
        self.default_agent = _normalize_optional_name(default_agent)

    async def run(
        self,
        user_text: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
        *,
        agent: "str | AgentProfile | None" = None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> RuntimeLoopResult:
        skill_command = parse_skill_commands(user_text)
        run_metadata = self._base_run_metadata(metadata)
        structured_output_schema = _active_structured_output_schema(
            self.config,
            output_schema,
        )
        structured_output_active = structured_output_schema is not None
        structured_output_tool_id = DEFAULT_STRUCTURED_OUTPUT_TOOL_ID
        if structured_output_active:
            run_metadata["structured_output"] = True
            run_metadata["structured_output_tool_id"] = structured_output_tool_id
        run_metadata["skill_command"] = {
            "add": list(skill_command.add),
            "clear": skill_command.clear,
            "cleaned_text": skill_command.cleaned_text,
        }
        command_expansion = self._expand_command_prompt(
            skill_command.cleaned_text,
            run_metadata,
        )
        user_text_for_request = (
            command_expansion.text
            if command_expansion is not None
            else skill_command.cleaned_text
        )
        agent_mention = self._resolve_agent_mention(user_text_for_request)
        profile, selected_agent_source = self._resolve_run_agent_profile(
            agent,
            command_expansion,
            agent_mention,
        )
        if selected_agent_source == "mention" and agent_mention is not None:
            user_text_for_request = agent_mention.stripped_text
            run_metadata["agent_mention"] = agent_mention.name
        iteration_limit = _profile_max_iterations(profile, self.config.max_iterations)
        base_active_skills = (
            _profile_active_skills(profile)
            if profile is not None
            else self.active_skills
        )
        active_skills = _apply_skill_command(base_active_skills, skill_command)
        active_skills = _visible_skill_names_for_permissions(
            active_skills,
            tool_permissions=self.config.tool_permissions,
        )
        command_tools = _command_tool_overrides(command_expansion)
        run_tools = _merge_run_tools(profile, command_tools, tools)
        run_tool_runtime = (
            _tool_runtime_with_structured_output(
                self.tool_runtime,
                structured_output_schema,
                tool_id=structured_output_tool_id,
            )
            if structured_output_active
            else self.tool_runtime
        )
        resolved_session_id = session_id or self.store.create_session().session_id
        run_id = self.run_state.begin(resolved_session_id)
        try:
            self._inject_pending_background_task_results(resolved_session_id)
            run_metadata["max_iterations"] = iteration_limit
            run_metadata["run_id"] = run_id
            if selected_agent_source is not None:
                run_metadata["selected_agent_source"] = selected_agent_source
            self._annotate_skill_metadata(run_metadata, active_skills)
            system_prompt_messages = self._build_system_prompt_messages(run_metadata)
            agent_profile_messages = self._build_agent_profile_messages(profile)
            self._annotate_agent_profile_metadata(
                run_metadata,
                profile,
                prompt_context_count=len(agent_profile_messages),
            )
            structured_output_messages = (
                _structured_output_context_messages(structured_output_tool_id)
                if structured_output_active
                else []
            )
            instruction_context_messages = self._build_instruction_context_messages()
            skill_context_messages = self._build_skill_context_messages(active_skills)
            context_messages = [
                *system_prompt_messages,
                *structured_output_messages,
                *agent_profile_messages,
                *instruction_context_messages,
                *skill_context_messages,
            ]
            if profile is None:
                self.active_skills = active_skills

            run_metadata["system_prompt_context_count"] = len(system_prompt_messages)
            run_metadata["agent_prompt_context_count"] = len(agent_profile_messages)
            run_metadata["structured_output_context_count"] = len(
                structured_output_messages
            )
            run_metadata["instruction_context_count"] = len(instruction_context_messages)
            run_metadata["skill_context_count"] = len(skill_context_messages)
            user_parts = self._resolve_user_parts(user_text_for_request)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=run_tool_runtime,
                max_iterations=iteration_limit,
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
                tool_selection=_config_tool_selection(
                    self.config,
                    structured_output_tool_id=(
                        structured_output_tool_id
                        if structured_output_active
                        else None
                    ),
                ),
                compaction_summarizer=(
                    self.compaction_summarizer
                    if self.config.enable_compaction_summarizer
                    else None
                ),
            )
            result = await runner.run(
                user_text=user_text_for_request,
                user_parts=user_parts,
                session_id=resolved_session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                tools=run_tools,
                structured_output_required=structured_output_active,
                structured_output_tool_id=structured_output_tool_id,
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
        structured_output_schema = self.config.structured_output_schema
        structured_output_active = structured_output_schema is not None
        structured_output_tool_id = DEFAULT_STRUCTURED_OUTPUT_TOOL_ID
        run_tool_runtime = (
            _tool_runtime_with_structured_output(
                self.tool_runtime,
                structured_output_schema,
                tool_id=structured_output_tool_id,
            )
            if structured_output_active
            else self.tool_runtime
        )
        run_id = self.run_state.begin(session_id)
        try:
            self._inject_pending_background_task_results(session_id)
            run_metadata = self._base_run_metadata(metadata)
            run_metadata["run_id"] = run_id
            if structured_output_active:
                run_metadata["structured_output"] = True
                run_metadata["structured_output_tool_id"] = structured_output_tool_id
            active_skills = _visible_skill_names_for_permissions(
                self.active_skills,
                tool_permissions=self.config.tool_permissions,
            )
            self._annotate_skill_metadata(run_metadata, active_skills)
            run_metadata["resume"] = True
            system_prompt_messages = self._build_system_prompt_messages(run_metadata)
            structured_output_messages = (
                _structured_output_context_messages(structured_output_tool_id)
                if structured_output_active
                else []
            )
            instruction_context_messages = self._build_instruction_context_messages()
            skill_context_messages = self._build_skill_context_messages(active_skills)
            context_messages = [
                *system_prompt_messages,
                *structured_output_messages,
                *instruction_context_messages,
                *skill_context_messages,
            ]
            run_metadata["system_prompt_context_count"] = len(system_prompt_messages)
            run_metadata["structured_output_context_count"] = len(
                structured_output_messages
            )
            run_metadata["instruction_context_count"] = len(instruction_context_messages)
            run_metadata["skill_context_count"] = len(skill_context_messages)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=run_tool_runtime,
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
                tool_selection=_config_tool_selection(
                    self.config,
                    structured_output_tool_id=(
                        structured_output_tool_id
                        if structured_output_active
                        else None
                    ),
                ),
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
                structured_output_required=structured_output_active,
                structured_output_tool_id=structured_output_tool_id,
            )
        except asyncio.CancelledError:
            self.run_state.finish(session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self.run_state.finish(session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(session_id, result.status)
        return result

    async def compact_session(
        self,
        session_id: str,
        *,
        max_parts: int | None = None,
        max_chars: int | None = None,
        context_reserve_chars: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        force: bool = True,
    ) -> Session:
        self.store.get_session(session_id)
        history = self.store.read_history(session_id)
        budget = _manual_compaction_budget(
            self.config,
            max_parts=max_parts,
            max_chars=max_chars,
            context_reserve_chars=context_reserve_chars,
            force=force,
        )
        strategy = BudgetCompactionStrategy(budget=budget)
        operation_metadata = _manual_compaction_operation_metadata(
            metadata,
            force=force,
        )
        summary: str | None = None
        if (
            self.config.enable_compaction_summarizer
            and self.compaction_summarizer is not None
        ):
            preparation = await CompactionController(self.compaction_summarizer).prepare(
                history,
                session_id=session_id,
                metadata=operation_metadata,
                compaction_strategy=strategy,
            )
            result = preparation.result
            summary = preparation.summary
            compaction_metadata = dict(preparation.compaction_metadata)
        else:
            result = strategy.compact(history)
            compaction_metadata = (
                _manual_compaction_result_metadata(budget, result)
                if result.compacted
                else {}
            )

        if not result.compacted:
            return self.store.get_session(session_id)

        compaction_metadata.update(operation_metadata)
        compacted_messages = _apply_manual_compaction_metadata(
            result.messages,
            source_messages=history,
            summary=summary,
            metadata=compaction_metadata,
        )
        updated_session = self._replace_history(session_id, compacted_messages)
        message_id, part_id = _first_new_compaction_identifiers(
            updated_session.messages,
            source_messages=history,
        )
        self.event_bus.publish(
            RuntimeEvent(
                type="session_compacted",
                message="Session history compacted.",
                session_id=session_id,
                message_id=message_id,
                part_id=part_id,
                payload=dict(compaction_metadata),
            )
        )
        return updated_session

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

    def _inject_pending_background_task_results(
        self,
        session_id: str | None,
    ) -> list[str]:
        if not self.config.inject_background_task_results or session_id is None:
            return []
        manager = _background_task_manager(self.tool_runtime)
        if manager is None:
            return []
        pending_injections = getattr(manager, "pending_injections", None)
        if not callable(pending_injections):
            return []
        try:
            self.store.get_session(session_id)
        except KeyError:
            return []
        records = pending_injections(session_id=session_id)
        injected_task_ids: list[str] = []
        for record in records:
            task_id = str(getattr(record, "task_id", ""))
            metadata = {
                "source": "background_task.injected",
                "synthetic": True,
                "background_task_ids": [task_id],
            }
            self.store.append_message(
                session_id,
                role=MessageRole.USER,
                parts=[
                    MessagePart.text_part(
                        format_background_task_notification(record),
                        metadata=metadata,
                    )
                ],
                metadata=metadata,
                status="complete",
            )
            injected_task_ids.append(task_id)
        return injected_task_ids

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

    def _build_agent_profile_messages(self, profile: Any | None) -> list[Message]:
        if profile is None:
            return []
        prompt = _profile_prompt(profile)
        if not prompt.strip():
            return []
        metadata = {
            "kind": "agent_profile_context",
            "source": "agent_profile_prompt",
            "agent_name": _profile_name(profile),
        }
        return [
            Message(
                role=MessageRole.SYSTEM,
                parts=[MessagePart.text_part(prompt, metadata=metadata)],
                metadata=metadata,
                status="complete",
            )
        ]

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

    def _expand_command_prompt(
        self,
        user_text: str,
        run_metadata: dict[str, Any],
    ) -> CommandExpansionResult | None:
        if (
            not self.config.enable_command_expansion
            or self.command_registry is None
            or not user_text
        ):
            return None

        expansion = expand_command(
            user_text,
            self.command_registry,
            max_command_chars=self.config.max_command_chars,
        )
        if expansion is None:
            return None

        run_metadata["command_name"] = expansion.definition.name
        run_metadata["command_file"] = (
            str(expansion.definition.command_file)
            if expansion.definition.source == "file"
            else ""
        )
        run_metadata["command_arguments"] = expansion.arguments
        run_metadata["command_source"] = expansion.definition.source
        if expansion.definition.agent is not None:
            run_metadata["command_agent"] = expansion.definition.agent
        if expansion.definition.model is not None:
            run_metadata["command_model"] = expansion.definition.model
            run_metadata["requested_model"] = expansion.definition.model
        if "subtask" in expansion.definition.metadata:
            run_metadata["command_subtask"] = deepcopy(
                expansion.definition.metadata["subtask"]
            )
        run_metadata["command_metadata"] = deepcopy(expansion.definition.metadata)
        run_metadata["command_truncated"] = expansion.truncated
        run_metadata["command_original_chars"] = expansion.original_chars
        run_metadata["command_max_chars"] = expansion.max_chars
        return expansion

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

    def _resolve_agent_profile(self, agent: Any | None) -> Any | None:
        if agent is not None and not isinstance(agent, str):
            if not _is_agent_profile(agent):
                raise TypeError("agent must be an agent name, AgentProfile, or None")
            return agent

        requested = _normalize_optional_name(agent)
        if requested is None:
            requested = self.default_agent
        if requested is None:
            return None

        if self.agent_registry is None:
            raise _unknown_agent_profile_error(requested, None)

        resolve = getattr(self.agent_registry, "resolve", None)
        if not callable(resolve):
            raise TypeError("agent_registry must expose resolve(name)")

        try:
            profile = resolve(requested)
        except KeyError as exc:
            raise _unknown_agent_profile_error(requested, self.agent_registry) from exc

        if not _is_agent_profile(profile):
            raise TypeError("agent_registry.resolve(name) must return an AgentProfile")
        if _profile_name(profile) != requested:
            raise _unknown_agent_profile_error(requested, self.agent_registry)
        return profile

    def _resolve_run_agent_profile(
        self,
        agent: Any | None,
        command_expansion: CommandExpansionResult | None,
        agent_mention: _AgentMention | None,
    ) -> tuple[Any | None, str | None]:
        if agent is not None and not isinstance(agent, str):
            return self._resolve_agent_profile(agent), "caller"

        caller_agent = _normalize_optional_name(agent)
        if caller_agent is not None:
            return self._resolve_agent_profile(caller_agent), "caller"

        command_agent = _command_agent_name(command_expansion)
        if command_agent is not None:
            return self._resolve_agent_profile(command_agent), "command"

        if agent_mention is not None:
            return agent_mention.profile, "mention"

        if self.default_agent is not None:
            return self._resolve_agent_profile(None), "default"

        return None, None

    def _resolve_agent_mention(self, user_text: str) -> _AgentMention | None:
        candidate = _parse_agent_mention(user_text)
        if candidate is None or self.agent_registry is None:
            return None

        profile = _resolve_mentioned_agent_profile(self.agent_registry, candidate.name)
        if profile is None:
            return None
        return _AgentMention(
            name=candidate.name,
            profile=profile,
            stripped_text=candidate.stripped_text,
        )

    def _annotate_agent_profile_metadata(
        self,
        run_metadata: dict[str, Any],
        profile: Any | None,
        *,
        prompt_context_count: int,
    ) -> None:
        if profile is None:
            return
        profile_metadata = _profile_metadata(profile)
        run_metadata["agent_name"] = _profile_name(profile)
        run_metadata["agent_description"] = _profile_description(profile)
        run_metadata["agent_metadata"] = profile_metadata
        permission_overlay = normalize_agent_permission_overlay(profile_metadata)
        if permission_overlay:
            run_metadata[AGENT_PERMISSION_OVERLAY_METADATA_KEY] = permission_overlay
            run_metadata[AGENT_PERMISSION_OVERLAY_SOURCE_KEY] = (
                AGENT_PERMISSION_OVERLAY_SOURCE
            )
        run_metadata["agent_prompt_context_count"] = prompt_context_count
        max_iterations = _profile_configured_max_iterations(profile)
        if max_iterations is not None:
            run_metadata["agent_max_iterations"] = max_iterations

    def _annotate_skill_metadata(
        self,
        run_metadata: dict[str, Any],
        active_skills: Iterable[str],
    ) -> None:
        active = list(active_skills)
        run_metadata["active_skills"] = active
        run_metadata["active_skill_count"] = len(active)
        if self.skill_discovery is not None:
            visible_skills = _visible_skill_names_for_permissions(
                [skill.name for skill in self.skill_discovery.discover()],
                tool_permissions=self.config.tool_permissions,
            )
            run_metadata["available_skill_count"] = len(visible_skills)

    def _checkpoint_store_method(self, name: str):
        method = getattr(self.store, name, None)
        if not callable(method):
            raise TypeError("session store does not support checkpoints")
        return method

    def _replace_history(self, session_id: str, messages: Iterable[Message]) -> Session:
        method = getattr(self.store, "replace_history", None)
        if not callable(method):
            raise TypeError("session store does not support history replacement")
        return method(session_id, messages)


def _manual_compaction_budget(
    config: RuntimeConfig,
    *,
    max_parts: int | None,
    max_chars: int | None,
    context_reserve_chars: int | None,
    force: bool,
) -> ContextBudget:
    explicit_limit = max_parts is not None or max_chars is not None
    if force and not explicit_limit:
        return ContextBudget(
            max_parts=1,
            reserve_chars=context_reserve_chars if context_reserve_chars is not None else 0,
        )
    return ContextBudget(
        max_parts=max_parts if max_parts is not None else config.max_context_parts,
        max_chars=max_chars if max_chars is not None else config.max_context_chars,
        reserve_chars=(
            context_reserve_chars
            if context_reserve_chars is not None
            else config.context_reserve_chars
        ),
    )


def _manual_compaction_operation_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    force: bool,
) -> dict[str, Any]:
    operation_metadata = dict(metadata or {})
    operation_metadata["manual_compaction"] = True
    operation_metadata["compaction_trigger"] = "manual"
    operation_metadata["force"] = force
    return operation_metadata


def _manual_compaction_result_metadata(
    budget: ContextBudget,
    result: CompactionResult,
) -> dict[str, Any]:
    return {
        "max_parts": budget.max_parts,
        "max_chars": budget.max_chars,
        "reserve_chars": budget.reserve_chars,
        "compacted_part_count": result.compacted_part_count,
        "compacted_message_count": result.compacted_message_count,
        "compacted_tool_pair_count": result.compacted_tool_pair_count,
        "compacted_chars": result.compacted_chars,
        "kept_chars": result.kept_chars,
    }


def _apply_manual_compaction_metadata(
    messages: Iterable[Message],
    *,
    source_messages: Iterable[Message],
    summary: str | None,
    metadata: Mapping[str, Any],
) -> list[Message]:
    source_message_ids = {message.message_id for message in source_messages}
    marker = {
        "manual_compaction": True,
        "compaction_trigger": "manual",
    }
    updated_messages: list[Message] = []
    for message in messages:
        updated_message = deepcopy(message)
        if updated_message.message_id in source_message_ids:
            updated_messages.append(updated_message)
            continue

        updated_message.metadata.update(marker)
        for part in updated_message.parts:
            if part.type is not MessagePartType.COMPACTION or part.compaction is None:
                continue
            part.metadata.update(marker)
            if summary is not None:
                part.compaction.summary = summary
                part.text = summary
            part.compaction.auto = False
            part.compaction.metadata.update(dict(metadata))
        updated_messages.append(updated_message)
    return updated_messages


def _first_new_compaction_identifiers(
    messages: Iterable[Message],
    *,
    source_messages: Iterable[Message],
) -> tuple[str | None, str | None]:
    source_message_ids = {message.message_id for message in source_messages}
    for message in messages:
        if message.message_id in source_message_ids:
            continue
        for part in message.parts:
            if part.type is MessagePartType.COMPACTION:
                return message.message_id, part.part_id
    return None, None


def _normalize_optional_name(name: Any | None) -> str | None:
    if name is None:
        return None
    normalized = str(name).strip()
    return normalized or None


def _parse_agent_mention(text: str) -> _AgentMentionCandidate | None:
    if not text:
        return None

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue

        token_start = 0
        while (
            token_start < len(line)
            and line[token_start].isspace()
            and line[token_start] not in "\r\n"
        ):
            token_start += 1

        if token_start >= len(line) or line[token_start] != "@":
            return None

        token_end = token_start + 1
        while token_end < len(line) and not line[token_end].isspace():
            token_end += 1

        name = _normalize_optional_name(line[token_start + 1 : token_end])
        if name is None:
            return None

        rest = line[token_end:]
        rest_start = 0
        while (
            rest_start < len(rest)
            and rest[rest_start].isspace()
            and rest[rest_start] not in "\r\n"
        ):
            rest_start += 1

        stripped_lines = list(lines)
        stripped_lines[index] = rest[rest_start:]
        return _AgentMentionCandidate(
            name=name,
            stripped_text="".join(stripped_lines),
        )

    return None


def _resolve_mentioned_agent_profile(registry: Any, name: str) -> Any | None:
    get = getattr(registry, "get", None)
    if callable(get):
        try:
            profile = get(name)
        except KeyError:
            return None
        if profile is None:
            return None
    else:
        resolve = getattr(registry, "resolve", None)
        if not callable(resolve):
            return None
        try:
            profile = resolve(name)
        except KeyError:
            return None

    if not _is_agent_profile(profile):
        raise TypeError("agent_registry.resolve(name) must return an AgentProfile")
    if _profile_name(profile) != name:
        return None
    return profile


def _is_agent_profile(value: Any) -> bool:
    return all(
        hasattr(value, field_name)
        for field_name in (
            "name",
            "description",
            "prompt",
            "tools",
            "active_skills",
            "max_iterations",
            "metadata",
        )
    )


def _profile_name(profile: Any) -> str:
    return str(getattr(profile, "name", "")).strip()


def _profile_description(profile: Any) -> str:
    return str(getattr(profile, "description", "") or "")


def _profile_prompt(profile: Any) -> str:
    return str(getattr(profile, "prompt", "") or "")


def _profile_metadata(profile: Any) -> dict[str, Any]:
    metadata = getattr(profile, "metadata", {}) or {}
    if not isinstance(metadata, Mapping):
        raise TypeError("agent profile metadata must be a mapping")
    return dict(metadata)


def _profile_configured_max_iterations(profile: Any) -> int | None:
    max_iterations = getattr(profile, "max_iterations", None)
    if max_iterations is None:
        return None
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int):
        raise TypeError("agent profile max_iterations must be an int or None")
    if max_iterations < 1:
        raise ValueError("agent profile max_iterations must be at least 1")
    return max_iterations


def _profile_max_iterations(profile: Any | None, default: int) -> int:
    if profile is None:
        return default
    return _profile_configured_max_iterations(profile) or default


def _profile_active_skills(profile: Any) -> list[str]:
    return _unique_skill_names(getattr(profile, "active_skills", []) or [])


def _profile_tools(profile: Any | None) -> dict[str, bool] | None:
    if profile is None:
        return None
    tools = getattr(profile, "tools", None)
    if tools is None:
        return None
    return _copy_tool_overrides(tools)


def _command_agent_name(
    expansion: CommandExpansionResult | None,
) -> str | None:
    if expansion is None:
        return None
    return _normalize_optional_name(expansion.definition.agent)


def _command_tool_overrides(
    expansion: CommandExpansionResult | None,
) -> dict[str, bool] | None:
    if expansion is None or "tools" not in expansion.definition.metadata:
        return None

    raw_tools = expansion.definition.metadata.get("tools")
    if isinstance(raw_tools, Mapping):
        return _copy_tool_overrides(raw_tools)
    if isinstance(raw_tools, list):
        return {str(tool_id): True for tool_id in raw_tools}

    raise ValueError("command tools metadata must be a list or mapping")


def _merge_run_tools(
    profile: Any | None,
    command_tools: Mapping[str, bool] | None,
    caller_tools: Mapping[str, bool] | None,
) -> dict[str, bool] | None:
    merged = _profile_tools(profile)
    if command_tools is not None:
        command_overrides = _copy_tool_overrides(command_tools)
        if merged is None:
            merged = command_overrides
        else:
            merged.update(command_overrides)
    if caller_tools is None:
        return merged
    caller_overrides = _copy_tool_overrides(caller_tools)
    if merged is None:
        return caller_overrides
    merged.update(caller_overrides)
    return merged


def _copy_tool_overrides(tools: Mapping[str, bool]) -> dict[str, bool]:
    copied: dict[str, bool] = {}
    for tool_id, enabled in tools.items():
        if not isinstance(enabled, bool):
            raise TypeError("tool overrides must map tool ids to bool values")
        copied[str(tool_id)] = enabled
    return copied


def _unknown_agent_profile_error(
    requested: str,
    registry: Any | None,
) -> KeyError:
    available = _agent_registry_names(registry)
    available_text = ", ".join(available) if available else "<none>"
    return KeyError(
        f"Unknown agent profile: {requested}. Available agents: {available_text}"
    )


def _agent_registry_names(registry: Any | None) -> list[str]:
    if registry is None:
        return []
    names = getattr(registry, "names", None)
    if not callable(names):
        return []
    return [str(name) for name in names()]


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
        inject_background_task_results=config.inject_background_task_results,
        structured_output_schema=(
            None
            if config.structured_output_schema is None
            else deepcopy(config.structured_output_schema)
        ),
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
        command_directories=list(config.command_directories),
        enable_command_expansion=config.enable_command_expansion,
        max_command_chars=config.max_command_chars,
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
                tool_permissions=config.tool_permissions,
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
                        tool_permissions=config.tool_permissions,
                    )
                )
            if _skill_list_tool_enabled(config, skill_discovery=skill_discovery):
                registry.register(
                    build_skill_list_tool(
                        skill_discovery or SkillDiscovery([]),
                        tool_permissions=config.tool_permissions,
                    )
                )
    if external_tool_providers is not None:
        register_external_tools(
            registry,
            external_tool_providers,
            allow_override=external_tools_allow_override,
        )
    resolved_permission_evaluator = permission_evaluator
    if resolved_permission_evaluator is None:
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
            task_hint_enabled=_task_truncation_hint_enabled(
                registry=registry,
                config=config,
            ),
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
    task_hint_enabled: bool = False,
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
        task_hint_enabled=task_hint_enabled,
    )


def _task_truncation_hint_enabled(
    *,
    registry: ToolRegistry,
    config: RuntimeConfig,
) -> bool:
    if "task" not in registry.ids():
        return False
    selection = _config_tool_selection(config)
    if "task" in selection.forced_disabled:
        return False
    if "task" in selection.disabled:
        return False
    return selection.enabled is None or "task" in selection.enabled


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


def _resolve_command_registry(
    config: RuntimeConfig,
    *,
    command_registry: CommandRegistry | None,
) -> CommandRegistry | None:
    if command_registry is not None:
        return command_registry
    if not config.command_directories:
        return None
    return CommandRegistry.from_sources(command_directories=config.command_directories)


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


def _visible_skill_names_for_permissions(
    names: Iterable[str],
    *,
    tool_permissions: Mapping[str, Any] | None,
) -> list[str]:
    permission_config = PermissionConfig(tool_permissions)
    return [
        name
        for name in _unique_skill_names(names)
        if not is_permission_subject_hidden(
            permission_config,
            tool_id="skill",
            category="skill",
            subject=name,
        )
    ]


def _active_structured_output_schema(
    config: RuntimeConfig,
    output_schema: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    if output_schema is not None:
        return output_schema
    return config.structured_output_schema


def _tool_runtime_with_structured_output(
    tool_runtime: ToolRuntime,
    output_schema: Mapping[str, Any],
    *,
    tool_id: str,
) -> ToolRuntime:
    registry = ToolRegistry(tool_runtime.registry.list())
    registry.register(
        create_structured_output_tool(output_schema, tool_id=tool_id),
        replace=True,
    )
    return ToolRuntime(
        registry,
        permission_evaluator=tool_runtime.permission_evaluator,
        default_output_policy=tool_runtime.default_output_policy,
        output_truncator=tool_runtime.output_truncator,
    )


def _structured_output_context_messages(tool_id: str) -> list[Message]:
    metadata = {
        "context_type": "system_prompt",
        "source": "structured_output_reminder",
        "structured_output_tool_id": tool_id,
    }
    return [
        Message(
            role=MessageRole.SYSTEM,
            parts=[
                MessagePart.text_part(
                    STRUCTURED_OUTPUT_SYSTEM_PROMPT,
                    metadata=metadata,
                )
            ],
            metadata=metadata,
            status="complete",
        )
    ]


def _config_tool_selection(
    config: RuntimeConfig,
    *,
    structured_output_tool_id: str | None = None,
) -> ToolSelection:
    forced_disabled = (
        set(PLAN_MODE_MUTATING_TOOLS)
        if config.runtime_mode == "plan" and config.plan_mode_read_only
        else set()
    )
    enabled = None if config.enabled_tools is None else set(config.enabled_tools)
    if enabled is not None and structured_output_tool_id is not None:
        enabled.add(structured_output_tool_id)
    return ToolSelection(
        enabled=enabled,
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
