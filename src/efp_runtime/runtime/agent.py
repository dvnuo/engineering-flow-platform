"""High-level EFP runtime facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from html import escape
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Union

from ..compaction.controller import CompactionController, CompactionSummarizer
from ..compaction.prune import prune_old_tool_outputs
from ..compaction.strategy import (
    BudgetCompactionStrategy,
    CompactionResult,
    ContextBudget,
)
from ..commands import (
    CommandExpansionResult,
    CommandRegistry,
    CommandShellExecutionResult,
    apply_command_shell_execution_results,
    builtin_command_definitions,
    expand_command,
    find_command_shell_interpolations,
)
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
from ..session.query import (
    query_messages as _query_messages,
    query_sessions as _query_sessions,
    session_context_messages as _session_context_messages,
)
from ..session.revert import (
    finalize_session_revert_record,
    invalidate_session_snapshot_references,
    prepare_session_revert_record,
    revert_session_state,
    unrevert_session_state,
)
from ..session.store import InMemorySessionStore
from ..session.summary import collect_session_file_diffs, summarize_session_diffs
from ..session.todo import SessionTodoStore
from ..skills.commands import (
    SkillCommandResult,
    parse_skill_commands,
    parse_skill_slash_command_line,
)
from ..skills.context import SkillContextBuilder, available_skills_system_message
from ..skills.discovery import SkillDiscovery
from ..skills.tool import build_skill_tool
from ..system_prompt import SystemPromptBuilder
from ..tools.builtin import (
    DEFAULT_STRUCTURED_OUTPUT_TOOL_ID,
    create_core_tool_registry,
    create_plan_exit_tool,
    create_question_tool,
    create_structured_output_tool,
)
from ..tools.builtin.task import format_background_task_notification
from ..tools.definition import OutputPolicy, ToolContext
from ..tools.registry import ToolRegistry
from ..tools.runtime import ToolRuntime
from ..tools.selection import ToolSelection, resolve_tool_selection
from ..tools.truncation import ToolOutputTruncator, TruncationLimits
from ..types import Attachment, SkillPackage, ToolCall, ToolResult, new_id
from ..workspace_snapshots import (
    DEFAULT_MAX_RETAINED_SNAPSHOTS,
    WorkspaceSnapshot,
    WorkspaceSnapshotDiff,
    WorkspaceSnapshotStore,
)
from .config import RuntimeConfig
from .run_state import RuntimeRunState

if TYPE_CHECKING:
    from ..agents.profile import AgentProfile
    from ..agents.registry import AgentRegistry


# Matches the default `tool_id` of `create_question_tool`.
QUESTION_TOOL_ID = "question"

PLAN_MODE_MUTATING_TOOLS = {
    "apply_patch",
    "bash",
    "edit",
    "task",
    "write",
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


@dataclass(frozen=True)
class _CommandSubtaskExecution:
    user_text: str | None = None
    events: list[RuntimeEvent] = field(default_factory=list)


def _mime_from_data_uri(uri: str) -> str:
    """Parse the MIME type from a data: URI, defaulting to image/png."""
    text = str(uri or "")
    if text.startswith("data:"):
        mime = text[5:].split(",", 1)[0].split(";", 1)[0].strip()
        if mime:
            return mime
    return "image/png"


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
        max_context_tokens: int | None = None,
        context_reserve_chars: int | None = None,
        context_reserve_tokens: int | None = None,
        compaction_auto: bool | None = None,
        compaction_rewrite_stored_history: bool | None = None,
        compaction_tail_turns: int | None = None,
        compaction_preserve_recent_chars: int | None = None,
        compaction_preserve_recent_tokens: int | None = None,
        compaction_reserved_chars: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        store: SessionStore | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_runtime: ToolRuntime | None = None,
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
            max_context_tokens=max_context_tokens,
            context_reserve_chars=context_reserve_chars,
            context_reserve_tokens=context_reserve_tokens,
            compaction_auto=compaction_auto,
            compaction_rewrite_stored_history=compaction_rewrite_stored_history,
            compaction_tail_turns=compaction_tail_turns,
            compaction_preserve_recent_chars=compaction_preserve_recent_chars,
            compaction_preserve_recent_tokens=compaction_preserve_recent_tokens,
            compaction_reserved_chars=compaction_reserved_chars,
            metadata=metadata,
        )
        self.provider = provider
        self.adapter = adapter
        self.compaction_summarizer = compaction_summarizer
        self.question_broker = question_broker or QuestionBroker()
        self.store = store or InMemorySessionStore()
        # Materialised on first use only: see ``workspace_snapshot_store``.
        self._workspace_snapshot_store: WorkspaceSnapshotStore | None = None
        self.skill_discovery = _resolve_skill_discovery(
            config=self.config,
            skill_discovery=skill_discovery,
        )
        self.agent_registry = agent_registry
        self.default_agent = _normalize_optional_name(default_agent)
        self.tool_runtime = _resolve_tool_runtime(
            provider=provider,
            workspace_root=self.config.workspace_root,
            config=self.config,
            store=self.store,
            tool_registry=tool_registry,
            tool_runtime=tool_runtime,
            permission_evaluator=permission_evaluator,
            skill_discovery=self.skill_discovery,
            question_broker=self.question_broker,
            lsp_client=lsp_client,
            agent_registry=self.agent_registry,
        )
        self._todo_store = (
            _find_session_todo_store(self.tool_runtime.registry)
            or _store_session_todo_store(self.store)
            or SessionTodoStore()
        )
        self.skill_context_builder = _resolve_skill_context_builder(
            config=self.config,
            skill_discovery=self.skill_discovery,
            skill_context_builder=skill_context_builder,
        )
        self.command_registry = _resolve_command_registry(
            self.config,
            command_registry=command_registry,
            skill_discovery=self.skill_discovery,
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

    def create_session(
        self,
        session_id: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Session:
        return self.store.create_session(
            session_id=session_id,
            title=title,
            metadata=metadata,
        )

    def get_session(self, session_id: str) -> Session:
        return self.store.get_session(session_id)

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        replace_metadata: bool = False,
    ) -> Session:
        return self.store.update_session(
            session_id,
            title=title,
            metadata=metadata,
            replace_metadata=replace_metadata,
        )

    def switch_agent(self, session_id: str, agent: str) -> Session:
        if not isinstance(agent, str):
            raise TypeError("agent must be a non-empty string")
        agent_name = agent.strip()
        if not agent_name:
            raise ValueError("agent must be a non-empty string")
        if self.agent_registry is not None:
            self._resolve_agent_profile(agent_name)

        session = self.update_session(session_id, metadata={"agent": agent_name})
        self.event_bus.publish(
            RuntimeEvent(
                type="session.agent_switched",
                message="Session agent switched.",
                session_id=session_id,
                payload={"agent": agent_name},
            )
        )
        return session

    def switch_model(self, session_id: str, model: str | Mapping[str, Any]) -> Session:
        if isinstance(model, str):
            model_value: str | dict[str, Any] = model.strip()
            if not model_value:
                raise ValueError("model must be a non-empty string or mapping")
            session = self.update_session(
                session_id,
                metadata={
                    "model": model_value,
                    "requested_model": model_value,
                },
            )
        elif isinstance(model, Mapping):
            model_value = deepcopy(dict(model))
            metadata = deepcopy(self.store.get_session(session_id).metadata)
            metadata["model"] = model_value
            metadata.pop("requested_model", None)
            session = self.update_session(
                session_id,
                metadata=metadata,
                replace_metadata=True,
            )
        else:
            raise TypeError("model must be a non-empty string or mapping")

        self.event_bus.publish(
            RuntimeEvent(
                type="session.model_switched",
                message="Session model switched.",
                session_id=session_id,
                payload={"model": deepcopy(model_value)},
            )
        )
        return session

    def list_sessions(self) -> list[Session]:
        return self.store.list_sessions()

    def query_sessions(
        self,
        *,
        limit: int | None = None,
        order: str = "desc",
        cursor: Mapping[str, Any] | None = None,
        search: str | None = None,
        roots: bool = False,
        path: str | None = None,
        workspace_id: str | None = None,
        parent_session_id: str | None = None,
        start: str | None = None,
    ) -> list[Session]:
        return _query_sessions(
            self.list_sessions(),
            limit=limit,
            order=order,
            cursor=cursor,
            search=search,
            roots=roots,
            path=path,
            workspace_id=workspace_id,
            parent_session_id=parent_session_id,
            start=start,
        )

    def delete_session(self, session_id: str) -> bool:
        return self.store.delete_session(session_id)

    def fork_session(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        new_session_id: str | None = None,
    ) -> Session:
        return self.store.fork_session(
            session_id,
            message_id=message_id,
            new_session_id=new_session_id,
        )

    def session_children(self, parent_session_id: str) -> list[Session]:
        return [
            session
            for session in self.list_sessions()
            if session.metadata.get("parent_session_id") == parent_session_id
        ]

    def session_messages(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        order: str = "asc",
        cursor: Mapping[str, Any] | None = None,
    ) -> list[Message]:
        return _query_messages(
            self.store.read_history(session_id),
            limit=limit,
            order=order,
            cursor=cursor,
        )

    def session_context(self, session_id: str) -> list[Message]:
        return _session_context_messages(self.session_messages(session_id))

    def session_diff(
        self,
        session_id: str,
        message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return collect_session_file_diffs(
            self.store.read_history(session_id),
            message_id=message_id,
        )

    def summarize_session(
        self,
        session_id: str,
        message_id: str | None = None,
        *,
        include_diffs: bool = True,
    ) -> dict[str, Any]:
        summary = summarize_session_diffs(
            self.store.read_history(session_id),
            message_id=message_id,
        )
        summary["session_id"] = session_id
        if not include_diffs:
            summary.pop("diffs", None)
        return summary

    def revert_session(
        self,
        session_id: str,
        message_id: str | None = None,
        part_id: str | None = None,
        *,
        delete_added: bool = True,
    ) -> Session:
        return revert_session_state(
            store=self.store,
            session_id=session_id,
            workspace_snapshot_store=self.workspace_snapshot_store,
            message_id=message_id,
            part_id=part_id,
            delete_added=delete_added,
        )

    def unrevert_session(
        self,
        session_id: str,
        *,
        delete_added: bool = True,
    ) -> Session:
        return unrevert_session_state(
            store=self.store,
            session_id=session_id,
            workspace_snapshot_store=self.workspace_snapshot_store,
            delete_added=delete_added,
        )

    def get_todos(self, session_id: str | None = None) -> list[dict[str, str]]:
        return self._session_todo_store().get(session_id)

    def set_todos(
        self,
        session_id: str | None,
        todos: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        return self._session_todo_store().set(session_id, todos)

    def clear_todos(self, session_id: str | None = None) -> None:
        self._session_todo_store().clear(session_id)

    async def run(
        self,
        user_text: str,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
        *,
        agent: "str | AgentProfile | None" = None,
        output_schema: Mapping[str, Any] | None = None,
        attached_images: list[str] | None = None,
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
        run_metadata["skill_command"] = _skill_command_metadata(skill_command)
        command_expansion = self._expand_command_prompt(
            skill_command.cleaned_text,
            run_metadata,
        )
        if command_expansion is None:
            self._apply_skill_slash_command_fallback(skill_command, run_metadata)
        user_text_for_request = (
            command_expansion.text
            if command_expansion is not None
            else skill_command.cleaned_text
        )
        existing_session = self._existing_session(session_id)
        self._apply_session_model_default(run_metadata, existing_session)
        agent_mention = self._resolve_agent_mention(user_text_for_request)
        profile, selected_agent_source = self._resolve_run_agent_profile(
            agent,
            command_expansion,
            agent_mention,
            existing_session,
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
        resolved_session_id = self._ensure_run_session(session_id)
        run_id = self.run_state.begin(resolved_session_id)
        revert_record = None
        try:
            revert_record = self._prepare_session_revert_record(
                resolved_session_id,
                run_id=run_id,
                source="run",
            )
            self._inject_pending_background_task_results(resolved_session_id)
            _record_run_iteration_limit_metadata(
                run_metadata,
                max_iterations=iteration_limit,
            )
            run_metadata["run_id"] = run_id
            command_subtask_events: list[RuntimeEvent] = []
            if selected_agent_source is not None:
                run_metadata["selected_agent_source"] = selected_agent_source
            if command_expansion is not None:
                command_expansion = await self._interpolate_command_shell(
                    command_expansion,
                    tool_runtime=run_tool_runtime,
                    session_id=resolved_session_id,
                    run_id=run_id,
                    run_metadata=run_metadata,
                )
                user_text_for_request = command_expansion.text
                subtask_execution = await self._execute_command_subtask_if_requested(
                    command_expansion,
                    profile=profile,
                    tool_runtime=run_tool_runtime,
                    session_id=resolved_session_id,
                    run_id=run_id,
                    run_metadata=run_metadata,
                    run_tools=run_tools,
                )
                command_subtask_events.extend(subtask_execution.events)
                if subtask_execution.user_text is not None:
                    user_text_for_request = subtask_execution.user_text
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
            available_skill_context_messages = (
                self._build_available_skill_context_messages()
            )
            skill_context_messages = self._build_skill_context_messages(active_skills)
            context_messages = [
                *system_prompt_messages,
                *structured_output_messages,
                *agent_profile_messages,
            ]
            post_instruction_context_messages = [
                *available_skill_context_messages,
                *skill_context_messages,
            ]
            if profile is None:
                self.active_skills = active_skills

            self._annotate_system_prompt_context_metadata(
                run_metadata,
                system_prompt_messages,
            )
            run_metadata["agent_prompt_context_count"] = len(agent_profile_messages)
            run_metadata["structured_output_context_count"] = len(
                structured_output_messages
            )
            run_metadata["instruction_context_count"] = 0
            run_metadata["available_skill_context_count"] = len(
                available_skill_context_messages
            )
            run_metadata["skill_context_count"] = len(skill_context_messages)
            user_parts = self._resolve_user_parts(user_text_for_request)
            user_parts = self._merge_attached_images(
                user_parts, user_text_for_request, attached_images
            )
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=run_tool_runtime,
                max_iterations=iteration_limit,
                doom_loop_threshold=self.config.doom_loop_threshold,
                default_provider_id=self.config.default_provider_id,
                default_model=self.config.default_model,
                max_context_parts=self.config.max_context_parts,
                max_context_chars=self.config.max_context_chars,
                max_context_tokens=self.config.max_context_tokens,
                context_reserve_chars=self.config.context_reserve_chars,
                context_reserve_tokens=self.config.context_reserve_tokens,
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
                    question_tool_id=self._registered_question_tool_id(),
                ),
                compaction_summarizer=(
                    self.compaction_summarizer
                    if self.config.enable_compaction_summarizer
                    else None
                ),
                compaction_auto=self.config.compaction_auto,
                compaction_rewrite_stored_history=(
                    self.config.compaction_rewrite_stored_history
                ),
                compaction_tail_turns=self.config.compaction_tail_turns,
                compaction_preserve_recent_chars=(
                    self.config.compaction_preserve_recent_chars
                ),
                compaction_preserve_recent_tokens=(
                    self.config.compaction_preserve_recent_tokens
                ),
                compaction_reserved_chars=self.config.compaction_reserved_chars,
            )
            self._seed_persisted_question_answer(resolved_session_id)
            result = await runner.run(
                user_text=user_text_for_request,
                user_parts=user_parts,
                session_id=resolved_session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                context_message_provider=lambda metadata: [
                    *self._build_instruction_context_messages(metadata),
                    *post_instruction_context_messages,
                ],
                tools=run_tools,
                structured_output_required=structured_output_active,
                structured_output_tool_id=structured_output_tool_id,
            )
        except asyncio.CancelledError:
            self._release_session_revert_record(revert_record)
            self.run_state.finish(resolved_session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self._release_session_revert_record(revert_record)
            self.run_state.finish(resolved_session_id, LoopStatus.ERROR)
            raise

        result.runtime_events.extend(command_subtask_events)
        if command_expansion is not None:
            result.runtime_events.append(
                _command_executed_event(
                    session_id=resolved_session_id,
                    result=result,
                    run_metadata=run_metadata,
                )
            )
        self.run_state.finish(resolved_session_id, result.status)
        self._finalize_session_revert_record(
            revert_record,
            status=result.status,
        )
        return result

    async def run_command(
        self,
        command: str,
        arguments: str = "",
        input_text: str = "",
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
        *,
        agent: "str | AgentProfile | None" = None,
        output_schema: Mapping[str, Any] | None = None,
    ) -> RuntimeLoopResult:
        command_name = _normalize_direct_command_name(command)
        if not command_name:
            raise ValueError("command name cannot be empty")
        if command_name == "skill":
            raise ValueError(
                "'skill' cannot be invoked with run_command; use /skill instead"
            )
        if self.command_registry is None:
            raise ValueError("run_command requires a command registry")
        if not self.config.enable_command_expansion:
            raise ValueError("run_command requires command expansion to be enabled")

        definition = self.command_registry.get(command_name, refresh=True)
        if definition is None:
            raise ValueError(
                _unknown_direct_command_message(command_name, self.command_registry)
            )

        run_metadata = dict(metadata or {})
        run_metadata["command_invocation"] = "direct"
        return await self.run(
            _compose_slash_command_text(
                definition.name,
                arguments=arguments,
                input_text=input_text,
            ),
            session_id=session_id,
            metadata=run_metadata,
            tools=tools,
            agent=agent,
            output_schema=output_schema,
        )

    async def resume(
        self,
        session_id: str,
        metadata: dict[str, Any] | None = None,
        tools: Mapping[str, bool] | None = None,
    ) -> RuntimeLoopResult:
        session = self.store.get_session(session_id)
        profile, selected_agent_source = self._resolve_resume_agent_profile(session)
        iteration_limit = _profile_max_iterations(profile, self.config.max_iterations)
        base_active_skills = (
            _profile_active_skills(profile)
            if profile is not None
            else self.active_skills
        )
        run_tools = _merge_run_tools(profile, None, tools)
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
        revert_record = None
        try:
            revert_record = self._prepare_session_revert_record(
                session_id,
                run_id=run_id,
                source="resume",
            )
            self._inject_pending_background_task_results(session_id)
            run_metadata = self._base_run_metadata(metadata)
            self._apply_session_model_default(run_metadata, session)
            run_metadata["run_id"] = run_id
            _record_run_iteration_limit_metadata(
                run_metadata,
                max_iterations=iteration_limit,
            )
            if selected_agent_source is not None:
                run_metadata["selected_agent_source"] = selected_agent_source
            if structured_output_active:
                run_metadata["structured_output"] = True
                run_metadata["structured_output_tool_id"] = structured_output_tool_id
            active_skills = _visible_skill_names_for_permissions(
                base_active_skills,
                tool_permissions=self.config.tool_permissions,
            )
            self._annotate_skill_metadata(run_metadata, active_skills)
            run_metadata["resume"] = True
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
            available_skill_context_messages = (
                self._build_available_skill_context_messages()
            )
            skill_context_messages = self._build_skill_context_messages(active_skills)
            context_messages = [
                *system_prompt_messages,
                *structured_output_messages,
                *agent_profile_messages,
            ]
            post_instruction_context_messages = [
                *available_skill_context_messages,
                *skill_context_messages,
            ]
            self._annotate_system_prompt_context_metadata(
                run_metadata,
                system_prompt_messages,
            )
            run_metadata["agent_prompt_context_count"] = len(agent_profile_messages)
            run_metadata["structured_output_context_count"] = len(
                structured_output_messages
            )
            run_metadata["instruction_context_count"] = 0
            run_metadata["available_skill_context_count"] = len(
                available_skill_context_messages
            )
            run_metadata["skill_context_count"] = len(skill_context_messages)
            runner = RuntimeLoopRunner(
                store=self.store,
                provider=self.provider,
                adapter=self.adapter,
                tool_runtime=run_tool_runtime,
                max_iterations=iteration_limit,
                doom_loop_threshold=self.config.doom_loop_threshold,
                default_provider_id=self.config.default_provider_id,
                default_model=self.config.default_model,
                max_context_parts=self.config.max_context_parts,
                max_context_chars=self.config.max_context_chars,
                max_context_tokens=self.config.max_context_tokens,
                context_reserve_chars=self.config.context_reserve_chars,
                context_reserve_tokens=self.config.context_reserve_tokens,
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
                    question_tool_id=self._registered_question_tool_id(),
                ),
                compaction_summarizer=(
                    self.compaction_summarizer
                    if self.config.enable_compaction_summarizer
                    else None
                ),
                compaction_auto=self.config.compaction_auto,
                compaction_rewrite_stored_history=(
                    self.config.compaction_rewrite_stored_history
                ),
                compaction_tail_turns=self.config.compaction_tail_turns,
                compaction_preserve_recent_chars=(
                    self.config.compaction_preserve_recent_chars
                ),
                compaction_preserve_recent_tokens=(
                    self.config.compaction_preserve_recent_tokens
                ),
                compaction_reserved_chars=self.config.compaction_reserved_chars,
            )
            self._seed_persisted_question_answer(session_id)
            result = await runner.run(
                user_text="",
                session_id=session_id,
                metadata=run_metadata,
                context_messages=context_messages,
                context_message_provider=lambda metadata: [
                    *self._build_instruction_context_messages(metadata),
                    *post_instruction_context_messages,
                ],
                append_user_message=False,
                tools=run_tools,
                structured_output_required=structured_output_active,
                structured_output_tool_id=structured_output_tool_id,
            )
        except asyncio.CancelledError:
            self._release_session_revert_record(revert_record)
            self.run_state.finish(session_id, LoopStatus.CANCELLED)
            raise
        except Exception:
            self._release_session_revert_record(revert_record)
            self.run_state.finish(session_id, LoopStatus.ERROR)
            raise

        self.run_state.finish(session_id, result.status)
        self._finalize_session_revert_record(
            revert_record,
            status=result.status,
        )
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

    def prune_session_tool_outputs(self, session_id: str, **options: Any) -> Session:
        """Persistently clear old completed tool result content from a session."""

        current_session = self.store.get_session(session_id)
        if not self.config.compaction_prune:
            return current_session

        history = self.store.read_history(session_id)
        prune_options = {
            "protect_recent_chars": self.config.compaction_prune_protect_chars,
            "min_pruned_chars": self.config.compaction_prune_min_chars,
            "output_max_chars": self.config.compaction_tool_output_max_chars,
        }
        prune_options.update(options)
        result = prune_old_tool_outputs(history, **prune_options)
        if result.pruned_result_count == 0:
            return current_session

        updated_session = self._replace_history(session_id, result.messages)
        self.event_bus.publish(
            RuntimeEvent(
                type="session_tool_outputs_pruned",
                message="Old tool result content pruned.",
                session_id=session_id,
                payload=dict(result.metadata),
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

    def _seed_persisted_question_answer(self, session_id: str) -> None:
        """Hand the broker any answer the session is holding for this run.

        The question a run stops on is durable twice over -- an unpaired tool
        call in history, and `pending_question_request` in session metadata --
        so every later run replays it. The answer used to live only in this
        object's broker, seeded from the metadata of the one request that
        carried it, and `consume_answer` pops it. Any run that was not that
        request found no answer, re-raised the identical question, and never
        reached the model; the member's messages reached the transcript and
        nothing else. Reading it back from the session gives the two the same
        lifetime.
        """
        try:
            metadata = self.store.get_session(session_id).metadata
        except KeyError:
            return
        held = metadata.get("pending_question_answer")
        if not isinstance(held, Mapping):
            return
        tool_call_id = held.get("tool_call_id")
        answers = held.get("answers")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip() or answers is None:
            return
        answer_session_id = held.get("session_id")
        self.question_broker.seed_answer(
            answer_session_id if isinstance(answer_session_id, str) else session_id,
            tool_call_id,
            answers,
        )

    def pending_questions(self) -> list[dict[str, Any]]:
        return [
            _question_request_to_dict(request)
            for request in self.question_broker.pending()
        ]

    def _registered_question_tool_id(self) -> str | None:
        """The question tool's id, but only when this runtime really has it.

        Asked of the registry rather than of `enable_question_tool`, because the
        registry is what tool selection validates against: naming a tool it does
        not hold raises instead of quietly doing nothing.
        """
        return QUESTION_TOOL_ID if self.tool_runtime.registry.get(QUESTION_TOOL_ID) else None

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

    def create_workspace_snapshot(
        self,
        label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> WorkspaceSnapshot:
        return self._require_workspace_snapshot_store().create_snapshot(
            label=label,
            metadata=metadata,
        )

    def list_workspace_snapshots(self) -> list[WorkspaceSnapshot]:
        return self._require_workspace_snapshot_store().list_snapshots()

    def diff_workspace_snapshot(
        self,
        snapshot_id: str,
    ) -> list[WorkspaceSnapshotDiff]:
        return self._require_workspace_snapshot_store().diff_snapshot(snapshot_id)

    def restore_workspace_snapshot(
        self,
        snapshot_id: str,
        *,
        delete_added: bool = True,
    ) -> WorkspaceSnapshot:
        return self._require_workspace_snapshot_store().restore_snapshot(
            snapshot_id,
            delete_added=delete_added,
        )

    def delete_workspace_snapshot(self, snapshot_id: str) -> bool:
        return self._require_workspace_snapshot_store().delete_snapshot(snapshot_id)

    def _build_instruction_context_messages(
        self,
        metadata: Mapping[str, Any] | None = None,
    ):
        return self.instruction_context_builder.build_messages(metadata=metadata)

    def _build_system_prompt_messages(self, metadata: Mapping[str, Any]):
        return self.system_prompt_builder.build_messages(metadata=metadata)

    def _annotate_system_prompt_context_metadata(
        self,
        run_metadata: dict[str, Any],
        messages: list[Message],
    ) -> None:
        environment_messages = [
            message
            for message in messages
            if message.metadata.get("kind") == "environment_context"
            or message.metadata.get("source") == "environment_context"
        ]
        run_metadata["system_prompt_context_count"] = len(messages)
        run_metadata["environment_context_count"] = len(environment_messages)
        if environment_messages:
            model_id = environment_messages[0].metadata.get("model_id")
            if model_id is not None:
                run_metadata["environment_context_model"] = model_id
        else:
            run_metadata.pop("environment_context_model", None)

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

    def _build_available_skill_context_messages(self) -> list[Message]:
        if self.skill_discovery is None:
            return []
        message = available_skills_system_message(
            _visible_skills_for_permissions(
                self.skill_discovery.discover(),
                tool_permissions=self.config.tool_permissions,
            )
        )
        return [] if message is None else [message]

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

    def _merge_attached_images(self, user_parts, user_text: str, attached_images):
        """Append gateway-supplied images as ATTACHMENT parts on the user message
        so they reach the LLM request as image content (not just a text note)."""
        images = [str(uri or "").strip() for uri in (attached_images or []) if str(uri or "").strip()]
        if not images:
            return user_parts
        if user_parts is not None:
            parts = list(user_parts)
        elif str(user_text or "").strip():
            parts = [MessagePart.text_part(user_text)]
        else:
            parts = []
        for data_uri in images:
            parts.append(
                MessagePart.attachment_part(
                    Attachment(
                        mime_type=_mime_from_data_uri(data_uri),
                        url=data_uri,
                        metadata={"kind": "image", "source": "gateway.attached_images"},
                    ),
                    metadata={"source": "gateway.attached_images"},
                )
            )
        return parts

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
        run_metadata["command_file"] = _command_file_metadata(expansion.definition)
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

    def _apply_skill_slash_command_fallback(
        self,
        skill_command: SkillCommandResult,
        run_metadata: dict[str, Any],
    ) -> None:
        if self.skill_discovery is None:
            return

        slash_command = parse_skill_slash_command_line(skill_command.cleaned_text)
        if slash_command is None or slash_command.name == "skill":
            return

        skill = self.skill_discovery.get(slash_command.name)
        if skill is None:
            return

        skill_command.add.append(skill.name)
        skill_command.cleaned_text = slash_command.cleaned_text
        run_metadata["skill_command"] = _skill_command_metadata(skill_command)
        run_metadata["skill_slash_command"] = skill.name
        run_metadata["skill_slash_arguments"] = slash_command.arguments

    async def _interpolate_command_shell(
        self,
        expansion: CommandExpansionResult,
        *,
        tool_runtime: ToolRuntime,
        session_id: str,
        run_id: str,
        run_metadata: dict[str, Any],
    ) -> CommandExpansionResult:
        interpolations = find_command_shell_interpolations(
            expansion.command_content,
            template_mask=expansion.command_template_mask,
        )
        if not interpolations:
            run_metadata["command_shell_interpolation_count"] = 0
            run_metadata["command_shell_interpolations"] = []
            return expansion

        results: list[CommandShellExecutionResult] = []
        for interpolation in interpolations:
            tool_call_id = _command_shell_tool_call_id(
                expansion.definition.name,
                interpolation.index,
            )
            tool_call = ToolCall(
                tool_name="bash",
                arguments={
                    "command": interpolation.command,
                    "description": (
                        "Custom command shell interpolation "
                        f"{expansion.definition.name} #{interpolation.index}"
                    ),
                },
                call_id=tool_call_id,
            )
            result = await tool_runtime.execute(
                tool_call,
                context=ToolContext(
                    session_id=session_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool_name="bash",
                    metadata=_command_shell_context_metadata(
                        expansion,
                        interpolation.index,
                    ),
                    cancel_requested=lambda: self.run_state.is_cancelled(session_id),
                ),
            )
            self._publish_command_shell_tool_events(
                result.events,
                session_id=session_id,
                run_id=run_id,
                tool_call=tool_call,
            )
            results.append(
                CommandShellExecutionResult(
                    interpolation=interpolation,
                    tool_id=result.tool_name,
                    tool_call_id=result.call_id,
                    status=result.status,
                    success=result.success,
                    content=result.content,
                )
            )

        expansion = apply_command_shell_execution_results(expansion, results)
        run_metadata["command_shell_interpolation_count"] = len(results)
        run_metadata["command_shell_interpolations"] = [
            result.to_metadata() for result in results
        ]
        return expansion

    async def _execute_command_subtask_if_requested(
        self,
        expansion: CommandExpansionResult,
        *,
        profile: Any | None,
        tool_runtime: ToolRuntime,
        session_id: str,
        run_id: str,
        run_metadata: dict[str, Any],
        run_tools: Mapping[str, bool] | None,
    ) -> _CommandSubtaskExecution:
        requested = _command_subtask_requested(expansion, profile)
        run_metadata["command_subtask_requested"] = requested
        if not requested:
            run_metadata["command_subtask_executed"] = False
            return _CommandSubtaskExecution()

        task_available = _tool_enabled_for_run(
            "task",
            registry=tool_runtime.registry,
            config=self.config,
            run_tools=run_tools,
        )
        run_metadata["command_subtask_available"] = task_available
        if not run_metadata["command_subtask_available"]:
            run_metadata["command_subtask_executed"] = False
            return _CommandSubtaskExecution()

        task_id = new_id("command-task")
        subagent_type = _command_subtask_subagent_type(
            expansion,
            profile=profile,
            default_agent=self.default_agent,
        )
        description = expansion.definition.description or expansion.definition.name
        context_metadata = _command_subtask_context_metadata(
            expansion,
            session_id=session_id,
            run_id=run_id,
            task_id=task_id,
            subagent_type=subagent_type,
        )
        tool_call = ToolCall(
            tool_id="task",
            id=task_id,
            args={
                "description": description,
                "prompt": expansion.command_content,
                "subagent_type": subagent_type,
                "task_id": task_id,
                "command": expansion.definition.name,
            },
            metadata=context_metadata,
        )
        result = await tool_runtime.execute(
            tool_call,
            context=ToolContext(
                session_id=session_id,
                run_id=run_id,
                tool_call_id=tool_call.call_id,
                tool_name="task",
                metadata=context_metadata,
                cancel_requested=lambda: self.run_state.is_cancelled(session_id),
            ),
        )
        task_events = _normalize_direct_tool_events(
            result.events,
            session_id=session_id,
            run_id=run_id,
            tool_call=tool_call,
            payload_context={
                "source": "command.subtask",
                "command_name": expansion.definition.name,
                "task_id": task_id,
                "subagent_type": subagent_type,
            },
            force_tool_call_id=True,
            fill_empty_values=True,
        )

        _record_command_subtask_result_metadata(
            run_metadata,
            result=result,
            task_id=task_id,
            subagent_type=subagent_type,
        )
        if result.status != "success":
            run_metadata["command_subtask_executed"] = False
            return _CommandSubtaskExecution(events=task_events)

        run_metadata["command_subtask_executed"] = True
        task_content = _command_subtask_content(result)
        event = _command_subtask_completed_event(
            session_id=session_id,
            run_id=run_id,
            command_name=expansion.definition.name,
            task_id=task_id,
            subagent_type=subagent_type,
            status=result.status,
        )
        return _CommandSubtaskExecution(
            user_text=_render_command_subtask_prompt(
                expansion,
                task_content=task_content,
                task_id=task_id,
                subagent_type=subagent_type,
            ),
            events=[*task_events, event],
        )

    def _publish_command_shell_tool_events(
        self,
        events: Iterable[Any],
        *,
        session_id: str,
        run_id: str,
        tool_call: ToolCall,
    ) -> None:
        for event in _normalize_direct_tool_events(
            events,
            session_id=session_id,
            run_id=run_id,
            tool_call=tool_call,
        ):
            self.event_bus.publish(event)

    def _base_run_metadata(self, metadata: Mapping[str, Any] | None) -> dict[str, Any]:
        run_metadata = dict(self.config.metadata)
        run_metadata.update(metadata or {})
        _record_run_iteration_limit_metadata(
            run_metadata,
            max_iterations=self.config.max_iterations,
        )
        run_metadata["runtime_mode"] = self.config.runtime_mode
        run_metadata["plan_mode_read_only"] = self.config.plan_mode_read_only
        run_metadata["enable_question_tool"] = self.config.enable_question_tool
        run_metadata["default_provider_id"] = self.config.default_provider_id
        run_metadata["default_model"] = self.config.default_model
        run_metadata["model_aware_tool_selection_enabled"] = (
            self.config.model_aware_tool_selection
        )
        run_metadata["emit_llm_stream_events"] = self.config.emit_llm_stream_events
        if self.config.workspace_root is not None:
            run_metadata["workspace_root"] = str(self.config.workspace_root)
        run_metadata["tool_output_truncation_enabled"] = (
            self.config.workspace_root is not None
        )
        return run_metadata

    def _existing_session(self, session_id: str | None) -> Session | None:
        if session_id is None:
            return None
        try:
            return self.store.get_session(session_id)
        except KeyError:
            return None

    def _ensure_run_session(self, session_id: str | None) -> str:
        if session_id is None:
            return self.store.create_session().session_id
        try:
            self.store.get_session(session_id)
        except KeyError:
            self.store.create_session(session_id=session_id)
        return session_id

    def _prepare_session_revert_record(
        self,
        session_id: str,
        *,
        run_id: str,
        source: str,
    ):
        # The store is only reached when a snapshot is actually going to
        # be taken: touching it otherwise would build it on every turn, which
        # is exactly the per-request cost the lazy store exists to avoid.
        enabled = self._session_revert_snapshots_enabled()
        return prepare_session_revert_record(
            store=self.store,
            session_id=session_id,
            run_id=run_id,
            source=source,
            workspace_snapshot_store=(
                self.workspace_snapshot_store if enabled else None
            ),
            enable_workspace_snapshot=enabled,
            protect_workspace_snapshot=True,
        )

    def _finalize_session_revert_record(
        self,
        record,
        *,
        status: str,
    ) -> None:
        if record is None:
            return
        try:
            finalize_session_revert_record(
                store=self.store,
                record=record,
                status=status,
            )
        finally:
            self._release_session_revert_record(record)

    def _release_session_revert_record(self, record) -> None:
        if (
            record is None
            or not record.workspace_snapshot_protected
            or record.workspace_snapshot_id is None
            or self.workspace_snapshot_store is None
        ):
            return
        self.workspace_snapshot_store.release_snapshot_protection(
            record.workspace_snapshot_id
        )

    def _session_revert_snapshots_enabled(self) -> bool:
        if not self.config.enable_session_revert_snapshots:
            return False
        # ``workspace_root is None`` is exactly the condition under which there
        # is no snapshot store, and asking for the store here would build one
        # just to discover the feature is off.
        if self.config.workspace_root is None:
            return False
        return Path(self.config.workspace_root).exists()

    def _apply_session_model_default(
        self,
        run_metadata: dict[str, Any],
        session: Session | None,
    ) -> None:
        if session is None or "requested_model" in run_metadata:
            return

        session_requested_model = session.metadata.get("requested_model")
        if isinstance(session_requested_model, str):
            requested_model = session_requested_model.strip()
            if requested_model:
                run_metadata["requested_model"] = requested_model
                return

        session_model = session.metadata.get("model")
        if isinstance(session_model, str):
            requested_model = session_model.strip()
            if requested_model:
                run_metadata["requested_model"] = requested_model
                return
        if isinstance(session_model, Mapping) and "session_model" not in run_metadata:
            run_metadata["session_model"] = deepcopy(dict(session_model))

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
        session: Session | None,
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

        session_agent = _session_agent_name(session)
        if session_agent is not None:
            return self._resolve_agent_profile(session_agent), "session"

        if self.default_agent is not None:
            return self._resolve_agent_profile(None), "default"

        return None, None

    def _resolve_resume_agent_profile(
        self,
        session: Session,
    ) -> tuple[Any | None, str | None]:
        session_agent = _session_agent_name(session)
        if session_agent is not None:
            return self._resolve_agent_profile(session_agent), "session"

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

    @property
    def workspace_snapshot_store(self) -> WorkspaceSnapshotStore | None:
        """The workspace snapshot store, built on first use.

        ``None`` when the runtime has no workspace root, exactly as before.
        Otherwise the store is constructed on demand rather than in
        ``__init__``: constructing it walks the on-disk snapshot directory, and
        an ``AgentRuntime`` is built once per chat request. A runtime that
        never captures or restores a snapshot (the default, with
        ``enable_session_revert_snapshots`` off) must not pay to read a
        directory nothing is going to use — on a network volume that scan was
        the dominant cost of a turn.
        """
        if self.config.workspace_root is None:
            return None
        if self._workspace_snapshot_store is None:
            self._workspace_snapshot_store = WorkspaceSnapshotStore(
                self.config.workspace_root,
                on_snapshot_removed=self._invalidate_session_snapshot_references,
                retain_snapshots=self._retain_workspace_snapshots,
            )
        return self._workspace_snapshot_store

    def _require_workspace_snapshot_store(self) -> WorkspaceSnapshotStore:
        store = self.workspace_snapshot_store
        if store is None:
            raise TypeError("workspace snapshots require workspace_root")
        return store

    def _invalidate_session_snapshot_references(
        self,
        snapshot: WorkspaceSnapshot,
    ) -> None:
        session_id = snapshot.metadata.get("session_id")
        invalidate_session_snapshot_references(
            store=self.store,
            snapshot_id=snapshot.snapshot_id,
            session_id=session_id if isinstance(session_id, str) else None,
        )

    def _session_revert_snapshot_ids(
        self, session_id: str
    ) -> tuple[bool, Any, Any] | None:
        """(revert_active, workspace_snapshot_id, unrevert_snapshot_id)."""
        try:
            get_summary = getattr(self.store, "get_session_summary", None)
            if callable(get_summary):
                summary = get_summary(session_id)
                return (
                    summary.revert_active is True,
                    summary.workspace_snapshot_id,
                    summary.unrevert_snapshot_id,
                )
            session = self.store.get_session(session_id)
        except KeyError:
            return None
        revert_metadata = session.metadata.get("revert")
        if not isinstance(revert_metadata, Mapping):
            return None
        return (
            revert_metadata.get("active") is True,
            revert_metadata.get("workspace_snapshot_id"),
            revert_metadata.get("unrevert_snapshot_id"),
        )

    def _retain_workspace_snapshots(
        self,
        snapshots: Sequence[WorkspaceSnapshot],
    ) -> list[str]:
        """Snapshot ids to keep past the store's retention cap.

        ``snapshots`` arrives oldest-first. Two kinds must survive pruning:

        * the BEFORE snapshot a session's revert target points at — pruning it
          turns a later ``revert_session`` into a silent history-only trim;
        * the unrevert snapshot of a revert that is currently *active*.

        A revert target is published on every finished turn, so pinning one per
        session that has ever run grows with lifetime session count rather than
        with live work — unbounded on a long-lived gateway. Only the newest
        ``max_retained_snapshots`` revert targets are pinned, which keeps recent
        sessions (including the turn that just finished) revertible while making
        total retention independent of how many sessions ever ran. Sessions that
        fall out of the window lose their workspace target and their revert
        degrades to the reported history-only trim.
        """
        # Read the materialised store directly: this runs *from inside* the
        # store's own pruning, so going through the lazy property would be a
        # re-entrant construction.
        store = self._workspace_snapshot_store
        revert_target_budget = (
            store.max_retained_snapshots
            if store is not None
            else DEFAULT_MAX_RETAINED_SNAPSHOTS
        )
        revert_targets: list[str] = []
        retained: list[str] = []
        for snapshot in snapshots:
            purpose = snapshot.metadata.get("purpose")
            if purpose not in ("session_revert", "session_unrevert"):
                continue
            session_id = snapshot.metadata.get("session_id")
            if not isinstance(session_id, str):
                continue
            state = self._session_revert_snapshot_ids(session_id)
            if state is None:
                continue
            revert_active, workspace_snapshot_id, unrevert_snapshot_id = state
            if purpose == "session_revert":
                if workspace_snapshot_id == snapshot.snapshot_id:
                    revert_targets.append(snapshot.snapshot_id)
            elif revert_active and unrevert_snapshot_id == snapshot.snapshot_id:
                retained.append(snapshot.snapshot_id)
        if revert_target_budget > 0:
            retained.extend(revert_targets[-revert_target_budget:])
        return retained

    def _replace_history(self, session_id: str, messages: Iterable[Message]) -> Session:
        method = getattr(self.store, "replace_history", None)
        if not callable(method):
            raise TypeError("session store does not support history replacement")
        return method(session_id, messages)

    def _session_todo_store(self) -> SessionTodoStore:
        registry_store = _find_session_todo_store(self.tool_runtime.registry)
        if registry_store is not None:
            self._todo_store = registry_store
        else:
            store_todos = _store_session_todo_store(self.store)
            if store_todos is not None:
                self._todo_store = store_todos
        return self._todo_store


def _store_session_todo_store(store: SessionStore) -> SessionTodoStore | None:
    todo_store = getattr(store, "todo_store", None)
    if not callable(todo_store):
        return None
    store_value = todo_store()
    if isinstance(store_value, SessionTodoStore):
        return store_value
    return None


def _find_session_todo_store(registry: ToolRegistry) -> SessionTodoStore | None:
    for tool_id in ("todowrite",):
        tool = registry.get(tool_id)
        if tool is None:
            continue
        store = tool.runtime_metadata.get("todo_store")
        if isinstance(store, SessionTodoStore):
            return store
        todos_by_session = tool.runtime_metadata.get("todos_by_session")
        if isinstance(todos_by_session, MutableMapping):
            store = SessionTodoStore(todos_by_session)
            tool.runtime_metadata["todo_store"] = store
            tool.runtime_metadata["todos_by_session"] = store.todos_by_session
            return store
    return None


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


def _session_agent_name(session: Session | None) -> str | None:
    if session is None:
        return None
    return _normalize_optional_name(session.metadata.get("agent"))


def _command_shell_tool_call_id(command_name: str, index: int) -> str:
    safe_name = "".join(
        char if char.isalnum() or char in {"_", "-"} else "_"
        for char in command_name
    ).strip("_")
    return f"command_shell_{safe_name or 'command'}_{index}"


def _command_shell_context_metadata(
    expansion: CommandExpansionResult,
    index: int,
) -> dict[str, Any]:
    metadata = {
        "command_name": expansion.definition.name,
        "command_source": expansion.definition.source,
        "command_arguments": expansion.arguments,
        "command_shell_interpolation": True,
        "command_shell_interpolation_index": index,
        "command_metadata": deepcopy(expansion.definition.metadata),
    }
    command_file = _command_file_metadata(expansion.definition)
    if command_file:
        metadata["command_file"] = command_file
    return metadata


def _normalize_direct_tool_events(
    events: Iterable[Any],
    *,
    session_id: str,
    run_id: str,
    tool_call: ToolCall,
    payload_context: Mapping[str, Any] | None = None,
    force_tool_call_id: bool = False,
    fill_empty_values: bool = False,
) -> list[RuntimeEvent]:
    context_payload = _direct_tool_event_context_payload(
        run_id=run_id,
        tool_call=tool_call,
        payload_context=payload_context,
    )
    normalized: list[RuntimeEvent] = []
    for event in events:
        if isinstance(event, RuntimeEvent):
            if event.session_id is None or (
                fill_empty_values and event.session_id == ""
            ):
                event.session_id = session_id
            _fill_missing_payload_values(
                event.payload,
                context_payload,
                force_keys={"tool_call_id"} if force_tool_call_id else None,
                fill_empty_values=fill_empty_values,
            )
            normalized.append(event)
            continue

        normalized.append(
            RuntimeEvent(
                type="tool.event",
                session_id=session_id,
                payload={"event": event, **context_payload},
            )
        )
    return normalized


def _direct_tool_event_context_payload(
    *,
    run_id: str,
    tool_call: ToolCall,
    payload_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": run_id,
        "tool_id": tool_call.tool_name,
        "tool_name": tool_call.tool_name,
        "tool_call_id": tool_call.call_id,
    }
    if payload_context:
        payload.update(
            {
                str(key): value
                for key, value in payload_context.items()
                if value is not None and value != ""
            }
        )
    return payload


def _fill_missing_payload_values(
    payload: dict[str, Any],
    values: Mapping[str, Any],
    *,
    force_keys: set[str] | None = None,
    fill_empty_values: bool = False,
) -> None:
    force_keys = force_keys or set()
    for key, value in values.items():
        if key in force_keys or _payload_value_missing(
            payload,
            key,
            fill_empty_values=fill_empty_values,
        ):
            payload[key] = value


def _payload_value_missing(
    payload: Mapping[str, Any],
    key: str,
    *,
    fill_empty_values: bool,
) -> bool:
    if key not in payload:
        return True
    if not fill_empty_values:
        return False
    return payload.get(key) is None or payload.get(key) == ""


def _command_subtask_requested(
    expansion: CommandExpansionResult,
    profile: Any | None,
) -> bool:
    if _command_subtask_explicit_false(expansion):
        return False
    if _command_subtask_explicit_true(expansion):
        return True
    if expansion.definition.source == "skill":
        return False
    return _profile_metadata(profile).get("mode") == "subagent" if profile else False


def _command_subtask_explicit_true(expansion: CommandExpansionResult) -> bool:
    return any(
        _subtask_value_is_true(value)
        for value in _command_subtask_values(expansion)
    )


def _command_subtask_explicit_false(expansion: CommandExpansionResult) -> bool:
    return any(
        _subtask_value_is_false(value)
        for value in _command_subtask_values(expansion)
    )


def _command_subtask_values(expansion: CommandExpansionResult) -> list[Any]:
    values: list[Any] = [expansion.definition.subtask]
    if "subtask" in expansion.definition.metadata:
        values.append(expansion.definition.metadata["subtask"])
    return values


def _subtask_value_is_true(value: Any) -> bool:
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _subtask_value_is_false(value: Any) -> bool:
    if value is False:
        return True
    return isinstance(value, str) and value.strip().lower() == "false"


def _command_subtask_subagent_type(
    expansion: CommandExpansionResult,
    *,
    profile: Any | None,
    default_agent: str | None,
) -> str:
    if profile is not None:
        profile_name = _normalize_optional_name(_profile_name(profile))
        if profile_name is not None:
            return profile_name
    command_agent = _command_agent_name(expansion)
    if command_agent is not None:
        return command_agent
    if default_agent is not None:
        return default_agent
    return "general"


def _command_subtask_context_metadata(
    expansion: CommandExpansionResult,
    *,
    session_id: str,
    run_id: str,
    task_id: str,
    subagent_type: str,
) -> dict[str, Any]:
    metadata = {
        "command_subtask": True,
        "command_name": expansion.definition.name,
        "command_source": expansion.definition.source,
        "command_arguments": expansion.arguments,
        "command_metadata": deepcopy(expansion.definition.metadata),
        "session_id": session_id,
        "run_id": run_id,
        "task_id": task_id,
        "subagent_type": subagent_type,
    }
    command_file = _command_file_metadata(expansion.definition)
    if command_file:
        metadata["command_file"] = command_file
    return metadata


def _record_command_subtask_result_metadata(
    run_metadata: dict[str, Any],
    *,
    result: ToolResult,
    task_id: str,
    subagent_type: str,
) -> None:
    run_metadata["command_subtask_available"] = True
    run_metadata["command_subtask_task_id"] = task_id
    run_metadata["command_subtask_subagent_type"] = subagent_type
    run_metadata["command_subtask_result_status"] = result.status
    run_metadata["command_subtask_result_success"] = result.success
    if result.error is not None:
        run_metadata["command_subtask_result_error"] = result.error
    if result.metadata:
        run_metadata["command_subtask_result_metadata"] = deepcopy(result.metadata)
    output = result.output
    if isinstance(output, Mapping):
        output_metadata = output.get("metadata")
        if isinstance(output_metadata, Mapping):
            run_metadata["command_subtask_output_metadata"] = deepcopy(
                dict(output_metadata)
            )


def _command_subtask_content(result: ToolResult) -> str:
    output = result.output
    if isinstance(output, Mapping) and output.get("text") is not None:
        return str(output["text"])
    return result.content


def _render_command_subtask_prompt(
    expansion: CommandExpansionResult,
    *,
    task_content: str,
    task_id: str,
    subagent_type: str,
) -> str:
    attrs = [
        f'name="{escape(expansion.definition.name, quote=True)}"',
        f'agent="{escape(subagent_type, quote=True)}"',
        f'task_id="{escape(task_id, quote=True)}"',
    ]
    block = "\n".join(
        [
            f"<command_subtask_result {' '.join(attrs)}>",
            task_content,
            "</command_subtask_result>",
        ]
    )
    if expansion.remaining_text:
        return f"{block}\n\n{expansion.remaining_text}"
    return block


def _command_subtask_completed_event(
    *,
    session_id: str,
    run_id: str,
    command_name: str,
    task_id: str,
    subagent_type: str,
    status: str,
) -> RuntimeEvent:
    return RuntimeEvent(
        type="command.subtask.completed",
        message="Command subtask completed.",
        session_id=session_id,
        payload={
            "run_id": run_id,
            "command": command_name,
            "task_id": task_id,
            "subagent_type": subagent_type,
            "status": status,
            "success": status == "success",
        },
    )


def _command_file_metadata(definition: CommandDefinition) -> str:
    if definition.source not in {"file", "skill"}:
        return ""
    command_file = str(definition.command_file)
    return "" if command_file == "." else command_file


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


def _record_run_iteration_limit_metadata(
    metadata: dict[str, Any],
    *,
    max_iterations: int | None,
) -> None:
    if max_iterations is None:
        metadata.pop("max_iterations", None)
        metadata["max_iterations_unbounded"] = True
        return
    metadata["max_iterations"] = max_iterations
    metadata.pop("max_iterations_unbounded", None)


def _profile_max_iterations(profile: Any | None, default: int | None) -> int | None:
    if profile is None:
        return default
    configured = _profile_configured_max_iterations(profile)
    return configured if configured is not None else default


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


def _command_executed_event(
    *,
    session_id: str,
    result: RuntimeLoopResult,
    run_metadata: Mapping[str, Any],
) -> RuntimeEvent:
    final_message = result.final_assistant_message
    return RuntimeEvent(
        type="command.executed",
        message="Command executed.",
        session_id=session_id,
        message_id=final_message.message_id if final_message is not None else None,
        payload={
            "name": run_metadata.get("command_name"),
            "arguments": run_metadata.get("command_arguments", ""),
            "source": run_metadata.get("command_source"),
            "status": result.status,
            "run_id": run_metadata.get("run_id"),
            "command_metadata": deepcopy(run_metadata.get("command_metadata") or {}),
            "truncated": bool(run_metadata.get("command_truncated", False)),
            "original_chars": run_metadata.get("command_original_chars", 0),
            "max_chars": run_metadata.get("command_max_chars", 0),
        },
    )


def _normalize_direct_command_name(command: str) -> str:
    return str(command or "").strip().lstrip("/")


def _compose_slash_command_text(
    command: str,
    *,
    arguments: str = "",
    input_text: str = "",
) -> str:
    text = "/" + command + (" " + arguments if arguments else "")
    if input_text:
        text += "\n" + input_text
    return text


def _unknown_direct_command_message(
    command: str,
    registry: CommandRegistry,
) -> str:
    message = f"unknown command '{command}'"
    try:
        names = [info.name for info in registry.list(refresh=False)]
    except Exception:
        names = []
    if names:
        message += "; available commands: " + ", ".join(names)
    return message


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
    max_context_tokens: int | None = None,
    context_reserve_chars: int | None,
    context_reserve_tokens: int | None = None,
    metadata: Mapping[str, Any] | None,
    compaction_auto: bool | None = None,
    compaction_rewrite_stored_history: bool | None = None,
    compaction_tail_turns: int | None = None,
    compaction_preserve_recent_chars: int | None = None,
    compaction_preserve_recent_tokens: int | None = None,
    compaction_reserved_chars: int | None = None,
) -> RuntimeConfig:
    if config is None:
        return RuntimeConfig(
            workspace_root=workspace_root,
            max_iterations=max_iterations,
            doom_loop_threshold=3,
            max_context_parts=max_context_parts,
            max_context_chars=max_context_chars,
            max_context_tokens=max_context_tokens,
            context_reserve_chars=(
                context_reserve_chars if context_reserve_chars is not None else 0
            ),
            context_reserve_tokens=context_reserve_tokens,
            compaction_auto=True if compaction_auto is None else compaction_auto,
            compaction_rewrite_stored_history=(
                False
                if compaction_rewrite_stored_history is None
                else compaction_rewrite_stored_history
            ),
            compaction_tail_turns=(
                2 if compaction_tail_turns is None else compaction_tail_turns
            ),
            compaction_preserve_recent_chars=compaction_preserve_recent_chars,
            compaction_preserve_recent_tokens=compaction_preserve_recent_tokens,
            compaction_reserved_chars=compaction_reserved_chars,
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
        max_context_tokens=(
            max_context_tokens
            if max_context_tokens is not None
            else config.max_context_tokens
        ),
        context_reserve_chars=(
            context_reserve_chars
            if context_reserve_chars is not None
            else config.context_reserve_chars
        ),
        context_reserve_tokens=(
            context_reserve_tokens
            if context_reserve_tokens is not None
            else config.context_reserve_tokens
        ),
        default_provider_id=config.default_provider_id,
        default_model=config.default_model,
        enable_compaction_summarizer=config.enable_compaction_summarizer,
        compaction_auto=(
            compaction_auto if compaction_auto is not None else config.compaction_auto
        ),
        compaction_rewrite_stored_history=(
            compaction_rewrite_stored_history
            if compaction_rewrite_stored_history is not None
            else config.compaction_rewrite_stored_history
        ),
        compaction_prune=config.compaction_prune,
        compaction_tail_turns=(
            compaction_tail_turns
            if compaction_tail_turns is not None
            else config.compaction_tail_turns
        ),
        compaction_preserve_recent_chars=(
            compaction_preserve_recent_chars
            if compaction_preserve_recent_chars is not None
            else config.compaction_preserve_recent_chars
        ),
        compaction_preserve_recent_tokens=(
            compaction_preserve_recent_tokens
            if compaction_preserve_recent_tokens is not None
            else config.compaction_preserve_recent_tokens
        ),
        compaction_reserved_chars=(
            compaction_reserved_chars
            if compaction_reserved_chars is not None
            else config.compaction_reserved_chars
        ),
        compaction_tool_output_max_chars=config.compaction_tool_output_max_chars,
        compaction_prune_min_chars=config.compaction_prune_min_chars,
        compaction_prune_protect_chars=config.compaction_prune_protect_chars,
        provider_max_retries=config.provider_max_retries,
        provider_retry_backoff_seconds=config.provider_retry_backoff_seconds,
        provider_retry_backoff_multiplier=config.provider_retry_backoff_multiplier,
        enable_context_overflow_retry=config.enable_context_overflow_retry,
        enable_session_revert_snapshots=config.enable_session_revert_snapshots,
        emit_llm_stream_events=config.emit_llm_stream_events,
        track_usage=config.track_usage,
        usage_pricing=dict(config.usage_pricing),
        enabled_tools=(
            None if config.enabled_tools is None else list(config.enabled_tools)
        ),
        disabled_tools=list(config.disabled_tools),
        model_aware_tool_selection=config.model_aware_tool_selection,
        tool_permissions=dict(config.tool_permissions),
        runtime_mode=config.runtime_mode,
        enable_plan_tool=config.enable_plan_tool,
        plan_mode_read_only=config.plan_mode_read_only,
        enable_question_tool=config.enable_question_tool,
        enable_lsp_tool=config.enable_lsp_tool,
        inject_background_task_results=config.inject_background_task_results,
        structured_output_schema=(
            None
            if config.structured_output_schema is None
            else deepcopy(config.structured_output_schema)
        ),
        metadata=resolved_metadata,
        include_default_system_prompt=config.include_default_system_prompt,
        include_environment_context=config.include_environment_context,
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


def _default_task_runner(
    *,
    provider: Union[LLMProvider, ProviderCallable],
    workspace_root: str | Path,
    config: RuntimeConfig,
    store: SessionStore,
    agent_registry: "AgentRegistry | None",
):
    from ..agents.task_runner import create_subagent_task_runner

    return create_subagent_task_runner(
        provider=provider,
        workspace_root=workspace_root,
        profiles=agent_registry,
        base_config=config,
        store_factory=lambda: store,
    )


def _resolve_tool_runtime(
    *,
    provider: Union[LLMProvider, ProviderCallable],
    workspace_root: str | Path | None,
    config: RuntimeConfig,
    store: SessionStore,
    tool_registry: ToolRegistry | None,
    tool_runtime: ToolRuntime | None,
    permission_evaluator: PermissionEvaluator | None,
    skill_discovery: SkillDiscovery | None,
    question_broker: QuestionBroker,
    lsp_client: LSPClient | None,
    agent_registry: "AgentRegistry | None",
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
            task_runner = _default_task_runner(
                provider=provider,
                workspace_root=workspace_root,
                config=config,
                store=store,
                agent_registry=agent_registry,
            )
            registry = create_core_tool_registry(
                workspace_root,
                task_runner=task_runner,
                skill_discovery=skill_discovery,
                tool_permissions=config.tool_permissions,
                max_skill_sidecar_chars=config.max_skill_sidecar_chars,
                question_broker=question_broker,
                include_question_tool=config.enable_question_tool,
                instruction_resolver=instruction_resolver,
                lsp_client=lsp_client,
                include_lsp_tool=config.enable_lsp_tool,
                include_plan_tool=_plan_tool_enabled(config),
                todo_store=_store_session_todo_store(store),
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


def _tool_enabled_for_run(
    tool_id: str,
    *,
    registry: ToolRegistry,
    config: RuntimeConfig,
    run_tools: Mapping[str, bool] | None,
) -> bool:
    if registry.get(tool_id) is None:
        return False
    selection = _config_tool_selection(config)
    enabled_tool_ids = resolve_tool_selection(
        registry.ids(),
        enabled=selection.enabled,
        disabled=selection.disabled,
        forced_disabled=selection.forced_disabled,
        overrides=run_tools,
    )
    return tool_id in enabled_tool_ids


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
    skill_discovery: SkillDiscovery | None,
) -> CommandRegistry | None:
    if command_registry is not None:
        return command_registry
    return CommandRegistry.from_sources(
        definitions=builtin_command_definitions(config.workspace_root),
        command_directories=config.command_directories,
        skill_discovery=skill_discovery,
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
        include_environment_context=config.include_environment_context,
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


def _skill_command_metadata(command: SkillCommandResult) -> dict[str, Any]:
    return {
        "add": list(command.add),
        "clear": command.clear,
        "cleaned_text": command.cleaned_text,
    }


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


def _visible_skills_for_permissions(
    skills: Iterable[SkillPackage],
    *,
    tool_permissions: Mapping[str, Any] | None,
) -> list[SkillPackage]:
    permission_config = PermissionConfig(tool_permissions)
    return [
        skill
        for skill in skills
        if not is_permission_subject_hidden(
            permission_config,
            tool_id="skill",
            category="skill",
            subject=skill.name,
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
    question_tool_id: str | None = None,
) -> ToolSelection:
    forced_disabled = (
        set(PLAN_MODE_MUTATING_TOOLS)
        if config.runtime_mode == "plan" and config.plan_mode_read_only
        else set()
    )
    enabled = None if config.enabled_tools is None else set(config.enabled_tools)
    if enabled is not None and structured_output_tool_id is not None:
        enabled.add(structured_output_tool_id)
    # `enabled_tools` chooses what the model is shown; `enable_question_tool`
    # chooses whether the tool exists. A profile that sets both and omits
    # `question` from the list registers a tool nothing can reach, so the run
    # cannot ask about a decision even though it was told it could. The tool
    # only puts a card in front of the member, so admitting it does not widen
    # what an allowlisted assistant may touch. The caller passes the id only
    # when the registry really holds it.
    if enabled is not None and question_tool_id is not None:
        enabled.add(question_tool_id)
    return ToolSelection(
        enabled=enabled,
        disabled=set(config.disabled_tools),
        forced_disabled=forced_disabled,
    )


def _plan_tool_enabled(config: RuntimeConfig) -> bool:
    if config.enable_plan_tool is None:
        return config.runtime_mode == "plan"
    return bool(config.enable_plan_tool)


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
