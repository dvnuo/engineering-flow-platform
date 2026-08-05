"""Executable EFP runtime loop runner."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
import inspect
import json
from typing import Any, Callable, List, Optional, Union

from ..compaction.controller import CompactionController, CompactionSummarizer
from ..compaction.strategy import (
    BudgetCompactionStrategy,
    CompactionResult,
    ContextBudget,
    TailTurnCompactionStrategy,
)
from ..context.render import prepare_history_for_request
from ..event_bus import RuntimeEventBus
from ..events import RuntimeEvent
from ..llm.adapter import DefaultLLMEventAdapter, LLMEventAdapter
from ..llm.errors import (
    ProviderContextOverflowError,
    ProviderError,
    ProviderTransientError,
)
from ..llm.events import LLMEvent
from ..llm.models import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    ModelContextProfile,
    context_safety_margin_tokens,
    default_max_context_tokens,
    is_catalog_model_context_profile,
    resolve_model_context_profile,
)
from ..permissions import ASK, DENY, PermissionMetadata
from ..session.models import Message, MessagePart, MessagePartType, MessageRole, Session
from ..session.processor import RuntimeSession, SessionProcessor
from ..session.protocol import SessionStore
from ..session.status import RuntimeStatus
from ..tools.definition import ToolContext
from ..tools.runtime import ToolRuntime
from ..tools.selection import (
    ModelAwareToolSelection,
    ToolSelection,
    resolve_model_aware_tool_selection,
    resolve_tool_selection,
)
from ..types import ToolCall, ToolResult, new_id
from ..usage import (
    UsageSummary,
    estimate_cost,
    merge_usage,
    normalize_usage,
    validate_usage_pricing,
)
from .provider import LLMProvider, ProviderOutput, ProviderResult, RuntimeRequest
from .stream_events import bridge_llm_stream_events


# The context reserve is response headroom; it may never claim more than
# 1/MAX_RESERVE_BUDGET_DIVISOR of the prompt budget. See
# RuntimeLoopRunner._clamp_reserve_chars.
MAX_RESERVE_BUDGET_DIVISOR = 2

ContextMessageProvider = Callable[[Mapping[str, Any]], Iterable[Message]]


def _user_message_metadata(run_metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build persisted author metadata for a newly appended user message."""
    metadata: dict[str, Any] = {
        "source": "loop.user",
        "author_type": "human",
    }
    values = run_metadata or {}

    def _text(key: str) -> str | None:
        value = values.get(key)
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    portal_author_name = _text("portal_user_name")
    author_id = _text("portal_user_id")
    has_portal_identity = bool(author_id or portal_author_name)
    author_name = portal_author_name if has_portal_identity else _text("user_name")
    metadata["author_source"] = "portal" if has_portal_identity else "runtime"
    if author_id:
        metadata["author_id"] = author_id
    if author_name:
        metadata["author_name"] = author_name
    return metadata


class LoopStatus:
    COMPLETED = "completed"
    ERROR = "error"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED = "cancelled"
    WAITING_FOR_PERMISSION = "waiting_for_permission"
    WAITING_FOR_QUESTION = "waiting_for_question"


@dataclass
class RuntimeLoopResult:
    session_id: str
    final_assistant_message: Optional[Message]
    iterations: int
    status: str
    runtime_events: List[RuntimeEvent] = field(default_factory=list)
    pending_permission_request: Optional[dict[str, Any]] = None
    pending_question_request: Optional[dict[str, Any]] = None
    usage: dict[str, Any] = field(default_factory=dict)
    structured_output: Optional[dict[str, Any]] = None


ProviderCallable = Callable[[RuntimeRequest], ProviderResult]
CancelCallback = Callable[..., Any]


@dataclass
class _ToolExecutionOutcome:
    cancelled: bool = False
    pending_permission_request: Optional[dict[str, Any]] = None
    pending_question_request: Optional[dict[str, Any]] = None
    terminal: bool = False
    terminal_reason: Optional[str] = None
    structured_output: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class _CompactionReplayInfo:
    replayed_message_id: str
    replay_message_id: str
    compaction_trigger: str
    overflow_retry: bool = False
    auto_continue: bool = False


@dataclass(frozen=True)
class _StoredCompactionOutcome:
    history: list[Message]
    replay: _CompactionReplayInfo | None = None


_COMPACTION_REPLAY_CONTINUE_TEXT = (
    "Continue if you have next steps, or stop and ask for clarification if you are "
    "unsure how to proceed."
)

MAX_STEPS_REACHED_PROMPT = """CRITICAL - MAXIMUM STEPS REACHED

The maximum number of steps allowed for this task has been reached. Tools are disabled until next user input. Respond with text only.

STRICT REQUIREMENTS:
1. Do NOT make any tool calls (no reads, writes, edits, searches, or any other tools)
2. MUST provide a text response summarizing work done so far
3. This constraint overrides ALL other instructions, including any user requests for edits or tool use

Response must include:
- Statement that maximum steps for this agent have been reached
- Summary of what has been accomplished so far
- List of any remaining tasks that were not completed
- Recommendations for what should be done next

Any attempt to use tools is a critical violation. Respond with text ONLY."""


class _RuntimeEventLog(list):
    def __init__(self, event_bus: Optional[RuntimeEventBus] = None):
        super().__init__()
        self._event_bus = event_bus

    def append(self, event: RuntimeEvent) -> None:
        super().append(event)
        if self._event_bus is not None:
            self._event_bus.publish(event)

    def extend(self, events: Iterable[RuntimeEvent]) -> None:
        for event in events:
            self.append(event)


class RuntimeLoopRunner:
    """Small iterative EFP runtime orchestrator."""

    def __init__(
        self,
        *,
        store: SessionStore,
        provider: Union[LLMProvider, ProviderCallable],
        adapter: Optional[LLMEventAdapter] = None,
        tool_runtime: ToolRuntime,
        max_iterations: int | None = None,
        doom_loop_threshold: Optional[int] = 3,
        default_provider_id: str = DEFAULT_PROVIDER_ID,
        default_model: str = DEFAULT_MODEL_ID,
        max_context_parts: Optional[int] = None,
        max_context_chars: Optional[int] = None,
        max_context_tokens: int | None = None,
        context_reserve_chars: int = 0,
        context_reserve_tokens: int | None = None,
        event_bus: Optional[RuntimeEventBus] = None,
        is_cancelled: Optional[CancelCallback] = None,
        tool_selection: Optional[ToolSelection] = None,
        compaction_summarizer: Optional[CompactionSummarizer] = None,
        compaction_auto: bool = True,
        compaction_rewrite_stored_history: bool = False,
        compaction_tail_turns: int = 2,
        compaction_preserve_recent_chars: int | None = None,
        compaction_preserve_recent_tokens: int | None = None,
        compaction_reserved_chars: int | None = None,
        provider_max_retries: int = 2,
        provider_retry_backoff_seconds: float = 0.0,
        provider_retry_backoff_multiplier: float = 2.0,
        enable_context_overflow_retry: bool = True,
        emit_llm_stream_events: bool = True,
        track_usage: bool = True,
        usage_pricing: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if doom_loop_threshold is not None and doom_loop_threshold < 2:
            raise ValueError("doom_loop_threshold must be at least 2 or None")
        # Must stay acceptance-identical to RuntimeConfig.__post_init__: this is
        # the second, hand-written entry point for the same knobs.
        _validate_optional_positive_int(
            max_context_parts,
            field_name="max_context_parts",
        )
        _validate_optional_positive_int(
            max_context_chars,
            field_name="max_context_chars",
        )
        default_provider_id = _validate_non_empty_string(
            default_provider_id,
            field_name="default_provider_id",
        )
        default_model = _validate_non_empty_string(
            default_model,
            field_name="default_model",
        )
        # Zero is rejected here too; see RuntimeConfig.__post_init__ for why it
        # is not coerced to None.
        _validate_optional_positive_int(
            max_context_tokens,
            field_name="max_context_tokens",
        )
        _validate_non_negative_int(
            context_reserve_chars,
            field_name="context_reserve_chars",
        )
        _validate_optional_non_negative_int(
            context_reserve_tokens,
            field_name="context_reserve_tokens",
        )
        _validate_non_negative_int(
            compaction_tail_turns,
            field_name="compaction_tail_turns",
        )
        _validate_optional_non_negative_int(
            compaction_preserve_recent_chars,
            field_name="compaction_preserve_recent_chars",
        )
        _validate_optional_non_negative_int(
            compaction_preserve_recent_tokens,
            field_name="compaction_preserve_recent_tokens",
        )
        _validate_optional_non_negative_int(
            compaction_reserved_chars,
            field_name="compaction_reserved_chars",
        )
        if provider_max_retries < 0:
            raise ValueError("provider_max_retries must be greater than or equal to 0")
        if provider_retry_backoff_seconds < 0:
            raise ValueError(
                "provider_retry_backoff_seconds must be greater than or equal to 0"
            )
        if provider_retry_backoff_multiplier < 1:
            raise ValueError(
                "provider_retry_backoff_multiplier must be greater than or equal to 1"
            )
        self.store = store
        self.provider = provider
        self.adapter = adapter or DefaultLLMEventAdapter()
        self.tool_runtime = tool_runtime
        self.max_iterations = max_iterations
        self.doom_loop_threshold = doom_loop_threshold
        self.default_provider_id = default_provider_id
        self.default_model = default_model
        self.max_context_parts = max_context_parts
        self.max_context_chars = max_context_chars
        self.max_context_tokens = max_context_tokens
        self.context_reserve_chars = context_reserve_chars
        self.context_reserve_tokens = context_reserve_tokens
        self.event_bus = event_bus
        self.is_cancelled = is_cancelled
        self.tool_selection = _copy_tool_selection(tool_selection)
        self.compaction_summarizer = compaction_summarizer
        self.compaction_auto = bool(compaction_auto)
        # Not bool(): this flag alone authorises rewriting the stored session,
        # and bool() fails open -- the string "false" is truthy. See the same
        # guard in RuntimeConfig.
        if not isinstance(compaction_rewrite_stored_history, bool):
            raise ValueError("compaction_rewrite_stored_history must be a boolean")
        self.compaction_rewrite_stored_history = compaction_rewrite_stored_history
        self.compaction_tail_turns = compaction_tail_turns
        self.compaction_preserve_recent_chars = compaction_preserve_recent_chars
        self.compaction_preserve_recent_tokens = compaction_preserve_recent_tokens
        self.compaction_reserved_chars = compaction_reserved_chars
        self.provider_max_retries = provider_max_retries
        self.provider_retry_backoff_seconds = provider_retry_backoff_seconds
        self.provider_retry_backoff_multiplier = provider_retry_backoff_multiplier
        self.enable_context_overflow_retry = enable_context_overflow_retry
        self.emit_llm_stream_events = bool(emit_llm_stream_events)
        self.track_usage = bool(track_usage)
        self.usage_pricing = validate_usage_pricing(usage_pricing or {})

    async def run(
        self,
        *,
        user_text: str,
        session_id: Optional[str] = None,
        session: Optional[Session] = None,
        max_iterations: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        context_messages: Optional[list[Message]] = None,
        context_message_provider: Optional[ContextMessageProvider] = None,
        append_user_message: bool = True,
        user_parts: Optional[List[MessagePart]] = None,
        tools: Optional[Mapping[str, bool]] = None,
        structured_output_required: bool = False,
        structured_output_tool_id: str = "StructuredOutput",
    ) -> RuntimeLoopResult:
        iteration_limit = max_iterations if max_iterations is not None else self.max_iterations
        if iteration_limit is not None and iteration_limit < 1:
            raise ValueError("max_iterations must be at least 1")

        run_metadata = dict(metadata or {})
        run_id = str(run_metadata.get("run_id") or new_id("run"))
        run_metadata["run_id"] = run_id
        run_metadata["emit_llm_stream_events"] = self.emit_llm_stream_events
        _record_usage_metadata(
            run_metadata,
            track_usage=self.track_usage,
            pricing_enabled=bool(self.usage_pricing),
        )

        all_tool_ids = self.tool_runtime.registry.ids()
        model_aware_selection = resolve_model_aware_tool_selection(
            all_tool_ids,
            run_metadata,
            enabled=_model_aware_tool_selection_enabled(run_metadata),
        )
        forced_disabled_tool_ids = set(self.tool_selection.forced_disabled)
        forced_disabled_tool_ids.update(model_aware_selection.forced_disabled)
        enabled_tool_ids = resolve_tool_selection(
            all_tool_ids,
            enabled=self.tool_selection.enabled,
            disabled=self.tool_selection.disabled,
            forced_disabled=forced_disabled_tool_ids,
            overrides=tools,
        )
        disabled_tool_ids = _disabled_tool_ids(
            all_tool_ids,
            enabled_tool_ids=enabled_tool_ids,
        )
        enabled_tool_id_set = set(enabled_tool_ids)
        enabled_tools = [
            self.tool_runtime.registry.require(tool_id) for tool_id in enabled_tool_ids
        ]

        resolved_session_id = self._ensure_session(
            session_id=session_id,
            session=session,
            allow_create=append_user_message,
        )
        appended_user_message: Message | None = None
        if append_user_message:
            if user_parts is not None:
                resolved_user_parts = list(user_parts)
            elif user_text:
                resolved_user_parts = [MessagePart.text_part(user_text)]
            else:
                resolved_user_parts = []
            appended_user_message = self.store.append_message(
                resolved_session_id,
                role=MessageRole.USER,
                parts=resolved_user_parts,
                metadata=_user_message_metadata(run_metadata),
                status="complete",
            )

        runtime_events: List[RuntimeEvent] = _RuntimeEventLog(self.event_bus)
        final_assistant_message: Optional[Message] = None
        status = LoopStatus.COMPLETED
        iterations = 0
        pending_permission_request: Optional[dict[str, Any]] = None
        pending_question_request: Optional[dict[str, Any]] = None
        terminal_reason: Optional[str] = None
        structured_output: Optional[dict[str, Any]] = None
        _record_model_aware_tool_selection_metadata(
            run_metadata,
            model_aware_selection=model_aware_selection,
        )
        _record_tool_selection_metadata(
            run_metadata,
            enabled_tool_ids=enabled_tool_ids,
            disabled_tool_ids=disabled_tool_ids,
        )
        run_metadata.update(self._model_context_budget_metadata(run_metadata))
        run_usage = _summary_with_estimated_cost(
            UsageSummary(),
            self.usage_pricing,
        )
        cancel_event_published = False

        def publish_cancelled(phase: str) -> None:
            nonlocal cancel_event_published
            if cancel_event_published:
                return
            cancel_event_published = True
            runtime_events.append(
                RuntimeEvent(
                    type="run_cancelled",
                    message="Runtime run was cancelled.",
                    session_id=resolved_session_id,
                    payload={
                        "run_id": run_id,
                        "phase": phase,
                        "iterations": iterations,
                    },
                )
            )

        run_start_payload = {
            "run_id": run_id,
            "enabled_tool_ids": list(enabled_tool_ids),
            "disabled_tool_ids": list(disabled_tool_ids),
            "model_aware_tool_selection": deepcopy(
                run_metadata["model_aware_tool_selection"]
            ),
            "emit_llm_stream_events": self.emit_llm_stream_events,
            "track_usage": self.track_usage,
            "usage_pricing_enabled": bool(
                self.track_usage and self.usage_pricing
            ),
        }
        _record_iteration_limit_metadata(
            run_start_payload,
            max_iterations=iteration_limit,
        )
        runtime_events.append(
            RuntimeEvent(
                type="run_start",
                session_id=resolved_session_id,
                payload=run_start_payload,
            )
        )

        while iteration_limit is None or iterations < iteration_limit:
            if await self._cancel_requested(resolved_session_id):
                status = LoopStatus.CANCELLED
                publish_cancelled("before_iteration")
                break

            pending_tool_calls = self._pending_tool_calls(resolved_session_id)
            if pending_tool_calls:
                final_assistant_message = _last_assistant_message(
                    self.store.read_history(resolved_session_id)
                )
                pending_outcome = await self._execute_tool_calls(
                    session_id=resolved_session_id,
                    tool_calls=pending_tool_calls,
                    runtime_events=runtime_events,
                    run_id=run_id,
                    run_metadata=run_metadata,
                    iteration=None,
                    resume_pending=True,
                    enabled_tool_ids=enabled_tool_id_set,
                )
                if pending_outcome.cancelled or await self._cancel_requested(
                    resolved_session_id
                ):
                    status = LoopStatus.CANCELLED
                    publish_cancelled("tool_execution")
                    break
                if pending_outcome.pending_permission_request is not None:
                    status = LoopStatus.WAITING_FOR_PERMISSION
                    pending_permission_request = pending_outcome.pending_permission_request
                    break
                if pending_outcome.pending_question_request is not None:
                    status = LoopStatus.WAITING_FOR_QUESTION
                    pending_question_request = pending_outcome.pending_question_request
                    break
                if pending_outcome.terminal:
                    status = LoopStatus.COMPLETED
                    terminal_reason = pending_outcome.terminal_reason
                    structured_output = pending_outcome.structured_output
                    break

            iteration = iterations + 1
            history = self.store.read_history(resolved_session_id)
            active_user_message = _active_user_message(
                history,
                preferred_message_id=(
                    appended_user_message.message_id
                    if appended_user_message is not None
                    else None
                ),
            )
            compaction_outcome = await self._maybe_compact_stored_session_history(
                session_id=resolved_session_id,
                history=history,
                active_user_message=active_user_message,
                runtime_events=runtime_events,
                run_id=run_id,
                iteration=iteration,
                trigger="context_budget",
                overflow=False,
                budget=self._context_budget(run_metadata),
                metadata=run_metadata,
            )
            history = compaction_outcome.history
            request_metadata = _request_metadata(
                run_metadata,
                session_id=resolved_session_id,
                iteration=iteration,
                max_iterations=iteration_limit,
            )
            max_steps_final_iteration = (
                iteration_limit is not None and iteration >= iteration_limit
            )
            request_tools = [] if max_steps_final_iteration else enabled_tools
            if max_steps_final_iteration:
                _record_max_steps_metadata(
                    request_metadata,
                    iteration=iteration,
                    max_iterations=iteration_limit,
                )
            _apply_compaction_replay_request_metadata(
                request_metadata,
                compaction_outcome.replay,
            )
            request_context_messages = _request_context_messages(
                context_messages=context_messages,
                context_message_provider=context_message_provider,
                metadata=request_metadata,
                run_metadata=run_metadata,
            )
            request_history = [*request_context_messages, *history]
            if max_steps_final_iteration:
                request_history.append(
                    _max_steps_reached_message(
                        iteration=iteration,
                        max_iterations=iteration_limit,
                    )
                )
            request = await self._prepare_runtime_request(
                session_id=resolved_session_id,
                history=history,
                request_history=request_history,
                iteration=iteration,
                max_iterations=iteration_limit,
                metadata=request_metadata,
                tools=request_tools,
                budget=self._context_budget(request_metadata),
            )
            _apply_compaction_replay_metadata_to_request(
                request,
                compaction_outcome.replay,
            )
            _append_request_compaction_event(
                runtime_events,
                request=request,
                session_id=resolved_session_id,
                run_id=run_id,
                iteration=iteration,
            )
            runtime_events.append(
                RuntimeEvent(
                    type="iteration_start",
                    session_id=resolved_session_id,
                    payload={"run_id": run_id, "iteration": iteration},
                )
            )

            iterations = iteration
            iteration_usage = _summary_with_estimated_cost(
                UsageSummary(),
                self.usage_pricing,
            )

            def record_step_usage(event: LLMEvent) -> None:
                nonlocal run_usage, iteration_usage
                if not self.track_usage:
                    return
                if event.type_value != "step_finish" or not event.usage:
                    return
                step_usage = _summary_with_estimated_cost(
                    normalize_usage(event.usage),
                    self.usage_pricing,
                )
                iteration_usage = _summary_with_estimated_cost(
                    merge_usage([iteration_usage, step_usage]),
                    self.usage_pricing,
                )
                run_usage = _summary_with_estimated_cost(
                    merge_usage([run_usage, step_usage]),
                    self.usage_pricing,
                )
                _annotate_latest_step_usage_event(
                    runtime_events,
                    step_usage=step_usage,
                    iteration_usage=iteration_usage,
                    run_usage=run_usage,
                )
                runtime_events.append(
                    RuntimeEvent(
                        type="usage.updated",
                        session_id=resolved_session_id,
                        payload={
                            "run_id": run_id,
                            "iteration": iteration,
                            "pricing_enabled": bool(self.usage_pricing),
                            "step_usage": _usage_payload(step_usage),
                            "iteration_usage": _usage_payload(iteration_usage),
                            "usage": _usage_payload(run_usage),
                        },
                    )
                )

            try:
                provider_output = await self._invoke_provider_with_retries(
                    request,
                    history=history,
                    tools=request_tools,
                    runtime_events=runtime_events,
                    run_id=run_id,
                    run_metadata=run_metadata,
                    context_messages=context_messages,
                    context_message_provider=context_message_provider,
                    iteration=iteration,
                    max_iterations=iteration_limit,
                )
                events = self._normalize_provider_output(provider_output)
                observed_events = bridge_llm_stream_events(
                    events,
                    runtime_events=runtime_events,
                    session_id=resolved_session_id,
                    run_id=run_id,
                    iteration=iteration,
                    enabled=self.emit_llm_stream_events,
                )
                observed_events = _observe_usage_events(
                    observed_events,
                    on_event=record_step_usage,
                )
                processor_session = RuntimeSession(
                    session_id=resolved_session_id,
                    messages=self.store.read_history(resolved_session_id),
                )
                processor = SessionProcessor(processor_session)
                assistant_message = await processor.consume(observed_events)
            except Exception as exc:  # noqa: BLE001 - runtime boundary reports provider failures.
                status = LoopStatus.ERROR
                runtime_events.append(
                    RuntimeEvent(
                        type="error",
                        message=str(exc) or exc.__class__.__name__,
                        session_id=resolved_session_id,
                        payload={
                            "run_id": run_id,
                            "iteration": iteration,
                            "phase": "provider",
                            "error_type": exc.__class__.__name__,
                            **_exception_event_payload(exc),
                        },
                    )
                )
                break

            if assistant_message is None:
                runtime_events.append(
                    RuntimeEvent(
                        type="loop.no_assistant_message",
                        session_id=resolved_session_id,
                        payload={"iteration": iteration},
                    )
                )
                break

            final_assistant_message = self._append_processed_message(
                resolved_session_id,
                assistant_message,
            )
            tool_calls = _assistant_tool_calls(final_assistant_message)
            iteration_finish_payload = {
                "run_id": run_id,
                "iteration": iteration,
                "tool_call_count": len(tool_calls),
            }
            if self.track_usage:
                iteration_finish_payload["usage"] = _usage_payload(iteration_usage)
                iteration_finish_payload["run_usage"] = _usage_payload(run_usage)
            runtime_events.append(
                RuntimeEvent(
                    type="iteration_finish",
                    session_id=resolved_session_id,
                    message_id=final_assistant_message.message_id,
                    payload=iteration_finish_payload,
                )
            )

            if processor.session.status is RuntimeStatus.ERROR:
                status = LoopStatus.ERROR
                runtime_events.append(
                    RuntimeEvent(
                        type="error",
                        message="Provider emitted an error.",
                        session_id=resolved_session_id,
                        message_id=final_assistant_message.message_id,
                        payload={"run_id": run_id, "iteration": iteration},
                    )
                )
                break
            if await self._cancel_requested(resolved_session_id):
                status = LoopStatus.CANCELLED
                publish_cancelled("after_iteration")
                break
            if max_steps_final_iteration:
                terminal_reason = "max_steps_reached"
                runtime_events.append(
                    RuntimeEvent(
                        type="loop.max_iterations",
                        message="Maximum steps reached; agent was asked to respond with text only.",
                        session_id=resolved_session_id,
                        message_id=final_assistant_message.message_id,
                        payload={
                            "run_id": run_id,
                            "max_iterations": iteration_limit,
                            "iteration": iteration,
                            "tool_call_count": len(tool_calls),
                            "tools_disabled": True,
                        },
                    )
                )
                if tool_calls:
                    status = LoopStatus.MAX_ITERATIONS
                else:
                    status = LoopStatus.COMPLETED
                break
            if not tool_calls:
                status = LoopStatus.COMPLETED
                break

            tool_execution_outcome = await self._execute_tool_calls(
                session_id=resolved_session_id,
                tool_calls=tool_calls,
                runtime_events=runtime_events,
                run_id=run_id,
                run_metadata=run_metadata,
                iteration=iteration,
                resume_pending=False,
                enabled_tool_ids=enabled_tool_id_set,
            )
            if tool_execution_outcome.cancelled or await self._cancel_requested(
                resolved_session_id
            ):
                status = LoopStatus.CANCELLED
                publish_cancelled("tool_execution")
                break
            if tool_execution_outcome.pending_permission_request is not None:
                status = LoopStatus.WAITING_FOR_PERMISSION
                pending_permission_request = tool_execution_outcome.pending_permission_request
                break
            if tool_execution_outcome.pending_question_request is not None:
                status = LoopStatus.WAITING_FOR_QUESTION
                pending_question_request = tool_execution_outcome.pending_question_request
                break
            if tool_execution_outcome.terminal:
                status = LoopStatus.COMPLETED
                terminal_reason = tool_execution_outcome.terminal_reason
                structured_output = tool_execution_outcome.structured_output
                break

        if status == LoopStatus.CANCELLED:
            publish_cancelled("finish")
        if structured_output_required and structured_output is None:
            prior_status = status
            if prior_status in (LoopStatus.COMPLETED, LoopStatus.MAX_ITERATIONS):
                status = LoopStatus.ERROR
                runtime_events.append(
                    RuntimeEvent(
                        type="structured_output.missing",
                        message="Structured output was requested but not provided.",
                        session_id=resolved_session_id,
                        message_id=(
                            final_assistant_message.message_id
                            if final_assistant_message is not None
                            else None
                        ),
                        payload={
                            "run_id": run_id,
                            "tool_id": structured_output_tool_id,
                            "iterations": iterations,
                            "prior_status": prior_status,
                        },
                    )
                )
        finish_payload = {
            "run_id": run_id,
            "status": status,
            "iterations": iterations,
        }
        if terminal_reason is not None:
            finish_payload["terminal_reason"] = terminal_reason
        if structured_output is not None:
            finish_payload["structured_output"] = True
        if self.track_usage:
            finish_payload["usage"] = _usage_payload(run_usage)
        runtime_events.append(
            RuntimeEvent(
                type="run_finish",
                session_id=resolved_session_id,
                message_id=(
                    final_assistant_message.message_id
                    if final_assistant_message is not None
                    else None
                ),
                payload=finish_payload,
            )
        )
        return RuntimeLoopResult(
            session_id=resolved_session_id,
            final_assistant_message=final_assistant_message,
            iterations=iterations,
            status=status,
            runtime_events=runtime_events,
            pending_permission_request=pending_permission_request,
            pending_question_request=pending_question_request,
            usage=_usage_payload(run_usage) if self.track_usage else {},
            structured_output=structured_output,
        )

    def _context_budget(self, metadata: Mapping[str, Any] | None = None) -> ContextBudget:
        profile = self._model_context_profile(metadata)
        max_chars = self._context_budget_max_chars(profile)
        reserve_chars = self._clamp_reserve_chars(
            self._context_budget_reserve_chars(profile),
            max_chars=max_chars,
        )
        return ContextBudget(
            max_parts=self.max_context_parts,
            max_chars=max_chars,
            reserve_chars=reserve_chars,
        )

    @staticmethod
    def _clamp_reserve_chars(reserve_chars: int, *, max_chars: int | None) -> int:
        """Keep the reserve from swallowing the whole prompt budget.

        ``ContextBudget.effective_max_chars`` is ``max_chars - reserve_chars``
        clamped at 0, and nothing else checks the two against each other. Before
        the catalog default, ``max_chars`` was ``None`` on an unconfigured
        runtime so the reserve knobs were inert; now they are always live, and a
        portal-managed ``context_reserve_tokens``/``compaction_reserved_chars``
        larger than the window would silently reduce every request to the system
        prompt plus the latest turn. The reserve is response headroom, so more
        than half the budget is never meaningful.
        """

        if max_chars is None:
            return reserve_chars
        return min(reserve_chars, max_chars // MAX_RESERVE_BUDGET_DIVISOR)

    def _context_budget_enabled(self, budget: Optional[ContextBudget] = None) -> bool:
        resolved = budget or self._context_budget()
        return resolved.max_parts is not None or resolved.max_chars is not None

    def _overflow_retry_budget(
        self,
        metadata: Mapping[str, Any] | None = None,
    ) -> ContextBudget:
        current_budget = self._context_budget(metadata)
        max_parts = self.max_context_parts
        max_chars = current_budget.max_chars
        if max_parts is not None:
            max_parts = max(1, min(max_parts, 2))
        if max_chars is not None:
            # Halve the *effective* budget, not the raw cap. Halving max_chars
            # directly does nothing once the reserve is a large share of it (the
            # retry would re-send an identically sized prompt), and can drive
            # effective_max_chars to 0, which compacts the whole conversation
            # away.
            effective = current_budget.effective_max_chars or max_chars
            max_chars = current_budget.reserve_chars + max(1, effective // 2)
        if max_parts is None and max_chars is None:
            # Unreachable from _context_budget, which always derives max_chars
            # from the model catalog; kept for hand-built budgets in tests.
            max_parts = 8
        return ContextBudget(
            max_parts=max_parts,
            max_chars=max_chars,
            reserve_chars=current_budget.reserve_chars,
        )

    def _explicit_context_budget_max_chars(
        self,
        profile: ModelContextProfile,
    ) -> int | None:
        """Return the operator's char budget before clamping, or None if unset."""

        if self.max_context_chars is not None:
            return self.max_context_chars
        if self.max_context_tokens is not None:
            return max(1, profile.tokens_to_chars(self.max_context_tokens))
        return None

    @staticmethod
    def _context_budget_max_chars_limit(profile: ModelContextProfile) -> int | None:
        """Return the largest char budget this model can be asked to accept.

        ``None`` means "no trustworthy ceiling exists, do not clamp". That is the
        case for every model that missed the catalog: ``resolve_model_context_profile``
        hands back the 64k conservative fallback, and a newly released or
        gateway-only model needs an operator override precisely because that
        guess is wrong about it. Clamping such an override to 64k would defeat
        the only purpose an override has.

        For a real catalog entry the ceiling is the same CAP the unset path
        produces (declared window minus the safety margin), rather than a novel
        third number. Clamping to the raw window instead would authorise a
        prompt sized at 100% of it, leaving nothing for the chars/4 estimate
        error the safety margin exists to absorb.

        Note this bounds ``max_chars``, not ``effective_max_chars``: the two
        routes subtract different reserves (see
        ``_context_budget_reserve_chars`` and the asymmetry pinned by
        ``test_tokens_and_chars_routes_keep_their_asymmetric_reserves``), so a
        clamped ``max_context_tokens`` ends up on the unset path's effective
        budget while a clamped ``max_context_chars`` keeps its zero reserve and
        so authorises a larger prompt - still under the declared window, since
        the safety margin is what the ceiling holds back.
        """

        if not is_catalog_model_context_profile(profile):
            return None
        return max(1, profile.tokens_to_chars(default_max_context_tokens(profile)))

    def _context_budget_max_chars(self, profile: ModelContextProfile) -> int | None:
        explicit = self._explicit_context_budget_max_chars(profile)
        if explicit is None:
            return max(1, profile.tokens_to_chars(default_max_context_tokens(profile)))
        limit = self._context_budget_max_chars_limit(profile)
        if limit is None:
            return explicit
        return min(explicit, limit)

    def _context_budget_reserve_chars(self, profile: ModelContextProfile) -> int:
        if self.context_reserve_tokens is not None:
            return profile.tokens_to_chars(self.context_reserve_tokens)
        if self.compaction_reserved_chars is not None:
            return self.compaction_reserved_chars
        if self.max_context_chars is None and self.context_reserve_chars == 0:
            return profile.tokens_to_chars(profile.default_reserve_tokens)
        return self.context_reserve_chars

    def _model_context_profile(
        self,
        metadata: Mapping[str, Any] | None,
    ) -> ModelContextProfile:
        requested_model = None
        if metadata is not None:
            requested_model = metadata.get("requested_model")
        if not isinstance(requested_model, str) or not requested_model.strip():
            requested_model = self.default_model
        return resolve_model_context_profile(
            requested_model,
            provider_id=self.default_provider_id,
        )

    def _compaction_preserve_recent_chars(
        self,
        profile: ModelContextProfile,
    ) -> int | None:
        if self.compaction_preserve_recent_tokens is not None:
            return profile.tokens_to_chars(self.compaction_preserve_recent_tokens)
        if self.compaction_preserve_recent_chars is not None:
            return self.compaction_preserve_recent_chars
        if self.max_context_chars is None:
            return profile.tokens_to_chars(profile.default_preserve_recent_tokens)
        return None

    def _model_context_budget_metadata(
        self,
        metadata: Mapping[str, Any] | None = None,
        *,
        budget: ContextBudget | None = None,
    ) -> dict[str, Any]:
        profile = self._model_context_profile(metadata)
        resolved_budget = budget or self._context_budget(metadata)
        requested_max_chars = self._explicit_context_budget_max_chars(profile)
        max_chars_limit = (
            self._context_budget_max_chars_limit(profile)
            if requested_max_chars is not None
            else None
        )
        max_chars_clamped = (
            max_chars_limit is not None and requested_max_chars > max_chars_limit
        )
        resolved_max_context_tokens = self.max_context_tokens
        safety_margin_tokens: int | None = None
        if self.max_context_chars is None and self.max_context_tokens is None:
            safety_margin_tokens = context_safety_margin_tokens(profile)
            resolved_max_context_tokens = default_max_context_tokens(profile)
        elif max_chars_clamped:
            # Once the clamp fires, the catalog ceiling IS the budget. Reporting
            # the operator's rejected number as ``max_context_tokens`` next to a
            # clamped ``max_context_chars`` would have the two disagree by the
            # whole overshoot; the original stays visible as
            # ``max_context_chars_requested``.
            safety_margin_tokens = context_safety_margin_tokens(profile)
            resolved_max_context_tokens = default_max_context_tokens(profile)
        preserve_recent_chars = self._compaction_preserve_recent_chars(profile)
        reserve_tokens = self.context_reserve_tokens
        if (
            reserve_tokens is None
            and self.max_context_chars is None
            and self.compaction_reserved_chars is None
            and self.context_reserve_chars == 0
        ):
            reserve_tokens = profile.default_reserve_tokens
        preserve_recent_tokens = self.compaction_preserve_recent_tokens
        if (
            preserve_recent_tokens is None
            and preserve_recent_chars is not None
            and self.compaction_preserve_recent_chars is None
            and self.max_context_chars is None
        ):
            preserve_recent_tokens = profile.default_preserve_recent_tokens
        max_context_chars_source = (
            "max_context_chars"
            if self.max_context_chars is not None
            else "max_context_tokens"
            if self.max_context_tokens is not None
            else "profile_context_window_tokens"
        )
        reserve_chars_source = (
            "context_reserve_tokens"
            if self.context_reserve_tokens is not None
            else "compaction_reserved_chars"
            if self.compaction_reserved_chars is not None
            else "profile_default_reserve_tokens"
            if reserve_tokens == profile.default_reserve_tokens
            and self.max_context_chars is None
            else "context_reserve_chars"
        )
        preserve_recent_chars_source = (
            "compaction_preserve_recent_tokens"
            if self.compaction_preserve_recent_tokens is not None
            else "compaction_preserve_recent_chars"
            if self.compaction_preserve_recent_chars is not None
            else "profile_default_preserve_recent_tokens"
            if preserve_recent_tokens == profile.default_preserve_recent_tokens
            and self.max_context_chars is None
            else None
        )
        payload = {
            "provider_id": profile.provider_id,
            "model_id": profile.model_id,
            "context_window_tokens": profile.context_window_tokens,
            "context_safety_margin_tokens": safety_margin_tokens,
            "max_context_tokens": resolved_max_context_tokens,
            "context_reserve_tokens": reserve_tokens,
            "compaction_preserve_recent_tokens": preserve_recent_tokens,
            "chars_per_token": profile.chars_per_token,
            "max_context_chars": resolved_budget.max_chars,
            "context_reserve_chars": resolved_budget.reserve_chars,
            "compaction_preserve_recent_chars": preserve_recent_chars,
        }
        payload["context_budget"] = {
            **payload,
            "max_context_chars_source": max_context_chars_source,
            "max_context_chars_requested": requested_max_chars,
            "max_context_chars_limit": max_chars_limit,
            "max_context_chars_clamped": max_chars_clamped,
            # Whether exceeding this budget also rewrites the STORED session.
            # Without it the only signal for the gate is the ABSENCE of
            # session_compaction_started/session_compacted, which is only
            # legible to someone who already knew to look - and the deployment
            # this flag changes most (a size knob set, stored rewriting now off,
            # session files growing) is exactly the one that would not know.
            "stored_history_rewrite": self.compaction_rewrite_stored_history,
            "context_reserve_chars_source": reserve_chars_source,
            "compaction_preserve_recent_chars_source": preserve_recent_chars_source,
            "max_parts": resolved_budget.max_parts,
            "max_chars": resolved_budget.max_chars,
            "reserve_chars": resolved_budget.reserve_chars,
        }
        return payload

    async def _maybe_compact_stored_session_history(
        self,
        *,
        session_id: str,
        history: list[Message],
        active_user_message: Message | None,
        runtime_events: List[RuntimeEvent],
        run_id: str,
        iteration: int,
        trigger: str,
        overflow: bool,
        budget: ContextBudget,
        metadata: Mapping[str, Any] | None = None,
        overflow_retry: bool = False,
    ) -> _StoredCompactionOutcome:
        if not self.compaction_auto:
            return _StoredCompactionOutcome(history=history)
        if not self._context_budget_enabled(budget):
            return _StoredCompactionOutcome(history=history)
        if not self.compaction_rewrite_stored_history:
            # Sizing the request and rewriting the transcript are separate
            # decisions, so they are separate knobs: a context budget - whether
            # catalog-derived or set by an operator - drives the in-memory
            # request only. Rewriting stored history discards the user's
            # transcript irreversibly, so it needs its own opt-in, including on
            # the provider-overflow retry, which does not need it: the retry
            # sends its halved budget through _prepare_runtime_request, and
            # prepare_history_for_request compacts at render time.
            return _StoredCompactionOutcome(history=history)
        if not history:
            return _StoredCompactionOutcome(history=history)

        model_context_metadata = self._model_context_budget_metadata(
            metadata,
            budget=budget,
        )
        profile = self._model_context_profile(metadata)
        preserve_recent_chars = self._compaction_preserve_recent_chars(profile)
        strategy = self._stored_session_compaction_strategy(
            budget,
            preserve_recent_chars=preserve_recent_chars,
        )
        tail_start_message_id = _tail_start_message_id(
            history,
            tail_turns=self.compaction_tail_turns,
        )
        operation_metadata = _auto_compaction_operation_metadata(
            budget=budget,
            trigger=trigger,
            overflow=overflow,
            overflow_retry=overflow_retry,
            tail_turns=self.compaction_tail_turns,
            preserve_recent_chars=preserve_recent_chars,
            tail_start_message_id=tail_start_message_id,
        )
        operation_metadata.update(model_context_metadata)
        summary: str | None = None
        if self.compaction_summarizer is not None:
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
                _compaction_result_metadata(budget, result)
                if result.compacted
                else {}
            )

        if not result.compacted:
            return _StoredCompactionOutcome(history=history)

        compaction_metadata.update(operation_metadata)
        compacted_messages, replay_info = _ensure_active_user_replay(
            result.messages,
            active_user_message=active_user_message,
            trigger=trigger,
            overflow_retry=overflow_retry,
        )
        if replay_info is not None:
            compaction_metadata.update(_compaction_replay_metadata(replay_info))
        compacted_messages = _apply_auto_compaction_metadata(
            compacted_messages,
            source_messages=history,
            summary=summary,
            metadata=compaction_metadata,
            overflow=overflow,
            tail_start_message_id=tail_start_message_id,
        )
        message_id, part_id = _first_new_compaction_identifiers(
            compacted_messages,
            source_messages=history,
        )
        event_payload = {
            "run_id": run_id,
            "iteration": iteration,
            **compaction_metadata,
        }
        runtime_events.append(
            RuntimeEvent(
                type="session_compaction_started",
                message="Automatic session compaction started.",
                session_id=session_id,
                message_id=message_id,
                part_id=part_id,
                payload=dict(event_payload),
            )
        )
        updated_session = self.store.replace_history(session_id, compacted_messages)
        updated_history = list(updated_session.messages)
        runtime_events.append(
            RuntimeEvent(
                type="session_compacted",
                message="Session history compacted.",
                session_id=session_id,
                message_id=message_id,
                part_id=part_id,
                payload={
                    **event_payload,
                    "stored_message_count": len(updated_history),
                    "stored": True,
                    "scope": "session",
                },
            )
        )
        return _StoredCompactionOutcome(history=updated_history, replay=replay_info)

    def _stored_session_compaction_strategy(
        self,
        budget: ContextBudget,
        *,
        preserve_recent_chars: int | None,
    ) -> BudgetCompactionStrategy:
        strategy_cls = globals().get("TailTurnCompactionStrategy")
        if strategy_cls is None:
            return BudgetCompactionStrategy(budget=budget)
        return strategy_cls(
            budget=budget,
            tail_turns=self.compaction_tail_turns,
            preserve_recent_chars=preserve_recent_chars,
        )

    async def _prepare_runtime_request(
        self,
        *,
        session_id: str,
        history: list[Message],
        request_history: list[Message],
        iteration: int,
        max_iterations: int | None,
        metadata: Mapping[str, Any],
        tools: list[Any],
        budget: ContextBudget,
    ) -> RuntimeRequest:
        request_metadata = dict(metadata)
        compaction_summary = None
        compaction_summary_metadata = None
        if self.compaction_summarizer is not None and self._context_budget_enabled(
            budget
        ):
            compaction_preparation = await CompactionController(
                self.compaction_summarizer
            ).prepare(
                request_history,
                session_id=session_id,
                budget=budget,
                metadata=request_metadata,
            )
            if compaction_preparation.compaction_applied:
                compaction_summary = compaction_preparation.summary
                compaction_summary_metadata = compaction_preparation.summary_metadata
        prepared_request = prepare_history_for_request(
            request_history,
            tools=tools,
            metadata=request_metadata,
            max_parts=budget.max_parts,
            max_chars=budget.max_chars,
            reserve_chars=budget.reserve_chars,
            compaction_summary=compaction_summary,
            compaction_summary_metadata=compaction_summary_metadata,
        )
        prepared_request = _with_model_context_compaction_metadata(
            prepared_request,
            self._model_context_budget_metadata(request_metadata, budget=budget),
        )
        return RuntimeRequest(
            session_id=session_id,
            messages=history,
            iteration=iteration,
            max_iterations=max_iterations,
            metadata=request_metadata,
            provider_request=prepared_request.request,
            prepared_request=prepared_request,
            tools=tools,
        )

    def _ensure_session(
        self,
        *,
        session_id: Optional[str],
        session: Optional[Session],
        allow_create: bool = True,
    ) -> str:
        if session is not None and session_id is not None and session.session_id != session_id:
            raise ValueError("session_id does not match session.session_id")

        resolved_session_id = session.session_id if session is not None else session_id
        if resolved_session_id is None:
            if not allow_create:
                raise ValueError("session_id or session is required when append_user_message=False")
            return self.store.create_session().session_id

        try:
            self.store.get_session(resolved_session_id)
            return resolved_session_id
        except KeyError:
            if not allow_create and session is None:
                raise
            pass

        if session is None:
            return self.store.create_session(session_id=resolved_session_id).session_id

        self.store.create_session(
            session_id=session.session_id,
            title=session.title,
            metadata=session.metadata,
        )
        for message in session.messages:
            self._append_processed_message(session.session_id, message)
        return session.session_id

    async def _invoke_provider(self, request: RuntimeRequest) -> ProviderOutput:
        if hasattr(self.provider, "invoke"):
            raw_result = self.provider.invoke(request)  # type: ignore[union-attr]
        elif callable(self.provider):
            raw_result = self.provider(request)
        else:
            raise TypeError("provider must expose invoke(request) or be callable")

        if inspect.isawaitable(raw_result):
            raw_result = await raw_result
        if isinstance(raw_result, Mapping) or not isinstance(raw_result, AsyncIterable):
            return raw_result
        # A streaming provider returns an *unstarted* async generator, and the
        # HTTP request only runs once it is iterated - which happens well outside
        # this retry loop. Pull the first chunk here so a context-overflow 400
        # surfaces as an exception the retry handler can act on. Later chunks
        # still stream lazily to the caller.
        return await _prefetch_async_stream(raw_result)

    async def _invoke_provider_with_retries(
        self,
        request: RuntimeRequest,
        *,
        history: list[Message],
        tools: list[Any],
        runtime_events: List[RuntimeEvent],
        run_id: str,
        run_metadata: dict[str, Any],
        context_messages: Optional[list[Message]],
        context_message_provider: Optional[ContextMessageProvider],
        iteration: int,
        max_iterations: int | None,
    ) -> ProviderOutput:
        current_request = request
        retry_count = 0
        overflow_retry_count = 0
        backoff_seconds = self.provider_retry_backoff_seconds

        while True:
            try:
                return await self._invoke_provider(current_request)
            except ProviderContextOverflowError as exc:
                _apply_provider_retry_metadata(
                    current_request,
                    retry_count=retry_count,
                    max_retries=self.provider_max_retries,
                    exc=exc,
                )
                if (
                    not self.enable_context_overflow_retry
                    or overflow_retry_count >= 1
                ):
                    raise
                overflow_retry_count += 1
                overflow_budget = self._overflow_retry_budget(current_request.metadata)
                overflow_metadata = _overflow_retry_metadata(
                    current_request.metadata,
                    attempt=overflow_retry_count,
                    budget=overflow_budget,
                )
                overflow_metadata.update(
                    self._model_context_budget_metadata(
                        current_request.metadata,
                        budget=overflow_budget,
                    )
                )
                retry_history = history
                replay_info: _CompactionReplayInfo | None = None
                if self.compaction_auto:
                    stored_history = self.store.read_history(current_request.session_id)
                    active_user_message = _active_user_message(stored_history)
                    compaction_outcome = await self._maybe_compact_stored_session_history(
                        session_id=current_request.session_id,
                        history=stored_history,
                        active_user_message=active_user_message,
                        runtime_events=runtime_events,
                        run_id=run_id,
                        iteration=iteration,
                        trigger="provider_context_overflow",
                        overflow=True,
                        overflow_retry=True,
                        budget=overflow_budget,
                        metadata=overflow_metadata,
                    )
                    retry_history = compaction_outcome.history
                    replay_info = compaction_outcome.replay
                _apply_compaction_replay_request_metadata(
                    overflow_metadata,
                    replay_info,
                )
                retry_context_messages = _request_context_messages(
                    context_messages=context_messages,
                    context_message_provider=context_message_provider,
                    metadata=overflow_metadata,
                    run_metadata=run_metadata,
                )
                retry_request_history = [
                    *retry_context_messages,
                    *retry_history,
                ]
                if max_iterations is not None and iteration >= max_iterations:
                    retry_request_history.append(
                        _max_steps_reached_message(
                            iteration=iteration,
                            max_iterations=max_iterations,
                        )
                    )
                overflow_request = await self._prepare_runtime_request(
                    session_id=current_request.session_id,
                    history=retry_history,
                    request_history=retry_request_history,
                    iteration=iteration,
                    max_iterations=max_iterations,
                    metadata=overflow_metadata,
                    tools=tools,
                    budget=overflow_budget,
                )
                _apply_compaction_replay_metadata_to_request(
                    overflow_request,
                    replay_info,
                )
                overflow_request = _with_provider_retry_metadata(
                    overflow_request,
                    retry_count=retry_count,
                    max_retries=self.provider_max_retries,
                    exc=exc,
                )
                _apply_overflow_retry_metadata(
                    overflow_request,
                    attempt=overflow_retry_count,
                    budget=overflow_budget,
                )
                overflow_event_payload = {
                    "run_id": run_id,
                    "iteration": iteration,
                    "attempt": overflow_retry_count,
                    "error_type": exc.__class__.__name__,
                    "code": exc.code,
                    "compaction": dict(
                        overflow_request.prepared_request.compaction_metadata
                    ),
                }
                overflow_event_payload.update(
                    _compaction_replay_payload_from_metadata(overflow_request.metadata)
                )
                runtime_events.append(
                    RuntimeEvent(
                        type="provider.context_overflow_retry",
                        message="Provider context overflow triggered compacted retry.",
                        session_id=current_request.session_id,
                        payload=overflow_event_payload,
                    )
                )
                current_request = overflow_request
            except ProviderTransientError as exc:
                if retry_count >= self.provider_max_retries:
                    _apply_provider_retry_metadata(
                        current_request,
                        retry_count=retry_count,
                        max_retries=self.provider_max_retries,
                        exc=exc,
                    )
                    raise
                retry_count += 1
                runtime_events.append(
                    _provider_retry_event(
                        session_id=current_request.session_id,
                        run_id=run_id,
                        iteration=iteration,
                        attempt=retry_count,
                        max_retries=self.provider_max_retries,
                        delay=backoff_seconds,
                        exc=exc,
                    )
                )
                current_request = _with_provider_retry_metadata(
                    current_request,
                    retry_count=retry_count,
                    max_retries=self.provider_max_retries,
                    exc=exc,
                )
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= self.provider_retry_backoff_multiplier
            except ProviderError as exc:
                if not exc.retryable or retry_count >= self.provider_max_retries:
                    _apply_provider_retry_metadata(
                        current_request,
                        retry_count=retry_count,
                        max_retries=self.provider_max_retries,
                        exc=exc,
                    )
                    raise
                retry_count += 1
                runtime_events.append(
                    _provider_retry_event(
                        session_id=current_request.session_id,
                        run_id=run_id,
                        iteration=iteration,
                        attempt=retry_count,
                        max_retries=self.provider_max_retries,
                        delay=backoff_seconds,
                        exc=exc,
                    )
                )
                current_request = _with_provider_retry_metadata(
                    current_request,
                    retry_count=retry_count,
                    max_retries=self.provider_max_retries,
                    exc=exc,
                )
                if backoff_seconds > 0:
                    await asyncio.sleep(backoff_seconds)
                    backoff_seconds *= self.provider_retry_backoff_multiplier

    def _normalize_provider_output(
        self,
        output: ProviderOutput,
    ) -> Union[Iterable[LLMEvent], AsyncIterable[LLMEvent]]:
        if isinstance(output, Mapping):
            return self.adapter.normalize_response(output)
        if isinstance(output, (str, bytes)):
            raise TypeError("provider output must be a mapping or LLMEvent iterable")
        if isinstance(output, (Iterable, AsyncIterable)):
            return output
        raise TypeError("provider output must be a mapping or LLMEvent iterable")

    async def _cancel_requested(self, session_id: str) -> bool:
        if self.is_cancelled is None:
            return False
        callback = self.is_cancelled
        try:
            signature = inspect.signature(callback)
            accepts_session = any(
                parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.VAR_POSITIONAL,
                )
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_session = True

        result = callback(session_id) if accepts_session else callback()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    def _append_processed_message(self, session_id: str, message: Message) -> Message:
        stored_message = _message_with_unique_part_ids(
            message,
            existing_part_ids=_session_part_ids(self.store.read_history(session_id)),
        )
        self.store.append_message(
            session_id,
            role=stored_message.role,
            parts=stored_message.parts,
            message_id=stored_message.message_id,
            parent_message_id=stored_message.parent_message_id,
            metadata=stored_message.metadata,
            status=stored_message.status,
            usage=stored_message.usage,
            completed_at=stored_message.completed_at,
        )
        return stored_message

    def _pending_tool_calls(self, session_id: str) -> List[ToolCall]:
        history = self.store.read_history(session_id)
        pairs = self.store.tool_pairs(session_id)
        for message in reversed(history):
            if message.role is not MessageRole.ASSISTANT:
                continue
            calls: List[ToolCall] = []
            for part in message.parts:
                if part.type is not MessagePartType.TOOL_CALL or part.tool_call is None:
                    continue
                pair = pairs.get(part.tool_call.call_id)
                if pair is None or pair[1] is None:
                    calls.append(part.tool_call)
            if calls:
                return calls
        return []

    async def _execute_tool_calls(
        self,
        *,
        session_id: str,
        tool_calls: List[ToolCall],
        runtime_events: List[RuntimeEvent],
        run_id: str,
        run_metadata: Mapping[str, Any],
        iteration: Optional[int],
        resume_pending: bool,
        enabled_tool_ids: set[str],
    ) -> _ToolExecutionOutcome:
        async def tool_cancel_requested() -> bool:
            return await self._cancel_requested(session_id)

        for tool_call in tool_calls:
            if await self._cancel_requested(session_id):
                return _ToolExecutionOutcome(cancelled=True)
            runtime_events.append(
                RuntimeEvent(
                    type="tool_call_start",
                    session_id=session_id,
                    payload=_tool_event_context_payload(
                        run_id=run_id,
                        tool_call=tool_call,
                        iteration=iteration,
                    ),
                )
            )
            if tool_call.tool_name == "_noop":
                result = _noop_tool_result(tool_call)
                runtime_events.append(
                    RuntimeEvent(
                        type="tool.ignored",
                        message=result.error,
                        session_id=session_id,
                        payload=_tool_event_context_payload(
                            run_id=run_id,
                            tool_call=tool_call,
                            iteration=iteration,
                        ),
                    )
                )
                self._append_tool_result(
                    session_id=session_id,
                    result=result,
                    runtime_events=runtime_events,
                    run_id=run_id,
                )
                continue
            if tool_call.tool_name not in enabled_tool_ids:
                result = _disabled_tool_result(tool_call)
                runtime_events.append(
                    RuntimeEvent(
                        type="tool.disabled",
                        message=result.error,
                        session_id=session_id,
                        payload=_tool_event_context_payload(
                            run_id=run_id,
                            tool_call=tool_call,
                            iteration=iteration,
                        ),
                    )
                )
                self._append_tool_result(
                    session_id=session_id,
                    result=result,
                    runtime_events=runtime_events,
                    run_id=run_id,
                )
                continue

            doom_loop_outcome = await self._evaluate_doom_loop_guard(
                session_id=session_id,
                tool_call=tool_call,
                runtime_events=runtime_events,
                run_id=run_id,
                run_metadata=run_metadata,
                iteration=iteration,
                resume_pending=resume_pending,
                cancel_requested=tool_cancel_requested,
            )
            if doom_loop_outcome is not None:
                if (
                    doom_loop_outcome.pending_permission_request is not None
                    or doom_loop_outcome.pending_question_request is not None
                    or doom_loop_outcome.cancelled
                ):
                    return doom_loop_outcome
                continue

            result = await self.tool_runtime.execute(
                tool_call,
                context=_tool_context(
                    session_id=session_id,
                    tool_call=tool_call,
                    run_id=run_id,
                    run_metadata=run_metadata,
                    iteration=iteration,
                    resume_pending=resume_pending,
                    messages=self.store.read_history(session_id),
                    cancel_requested=tool_cancel_requested,
                ),
            )
            permission_request = _permission_request_payload(result.metadata)
            question_request = _question_request_payload(result.metadata)
            permission_event_published = False
            question_event_published = False
            for event in result.events:
                if isinstance(event, RuntimeEvent):
                    if event.session_id is None:
                        event.session_id = session_id
                    _fill_tool_event_context(
                        event,
                        run_id=run_id,
                        tool_call=tool_call,
                        iteration=iteration,
                    )
                    if event.type == "tool.permission_requested":
                        permission_event_published = True
                        event.payload.update(
                            _permission_requested_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                permission_request=permission_request,
                            )
                        )
                    if event.type == "tool.question_requested":
                        question_event_published = True
                        event.payload.update(
                            _question_requested_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                question_request=question_request,
                            )
                        )
                    runtime_events.append(event)
                else:
                    runtime_events.append(
                        RuntimeEvent(
                            type="tool.event",
                            session_id=session_id,
                            payload={"event": event},
                        )
                    )

            if result.status == "permission_requested":
                if not permission_event_published:
                    runtime_events.append(
                        RuntimeEvent(
                            type="tool.permission_requested",
                            session_id=session_id,
                            message=result.content,
                            payload=_permission_requested_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                permission_request=permission_request,
                            ),
                        )
                    )
                return _ToolExecutionOutcome(
                    pending_permission_request=permission_request,
                )

            if result.status == "question_requested":
                if not question_event_published:
                    runtime_events.append(
                        RuntimeEvent(
                            type="tool.question_requested",
                            session_id=session_id,
                            message=result.content,
                            payload=_question_requested_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                question_request=question_request,
                            ),
                        )
                    )
                return _ToolExecutionOutcome(
                    pending_question_request=question_request,
                )

            self._append_tool_result(
                session_id=session_id,
                result=result,
                runtime_events=runtime_events,
                run_id=run_id,
            )
            if _is_terminal_tool_result(result):
                terminal_reason = _terminal_reason(result) or "tool_terminal"
                terminal_structured_output = _structured_output_from_terminal_result(
                    result
                )
                runtime_events.append(
                    RuntimeEvent(
                        type="tool_terminal",
                        message="Terminal tool result received.",
                        session_id=session_id,
                        payload={
                            **_tool_event_context_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                iteration=iteration,
                            ),
                            "terminal_reason": terminal_reason,
                            "tool_result_status": result.status,
                            "plan_status": result.metadata.get("plan_status"),
                        },
                    )
                )
                return _ToolExecutionOutcome(
                    terminal=True,
                    terminal_reason=terminal_reason,
                    structured_output=terminal_structured_output,
                )
        return _ToolExecutionOutcome()

    async def _evaluate_doom_loop_guard(
        self,
        *,
        session_id: str,
        tool_call: ToolCall,
        runtime_events: List[RuntimeEvent],
        run_id: str,
        run_metadata: Mapping[str, Any],
        iteration: Optional[int],
        resume_pending: bool,
        cancel_requested: Callable[[], Any] | None,
    ) -> Optional[_ToolExecutionOutcome]:
        threshold = self.doom_loop_threshold
        if threshold is None:
            return None

        repeat_count = _recent_tool_call_repeat_count(
            self.store.read_history(session_id),
            current=tool_call,
        )
        if repeat_count < threshold:
            return None

        arguments_json = _stable_tool_arguments_json(tool_call.arguments)
        reason = (
            "Repeated tool call detected: "
            f"{tool_call.tool_name} was requested {repeat_count} times "
            "with the same arguments."
        )
        metadata = PermissionMetadata(
            action=ASK,
            category="doom_loop",
            resource=tool_call.tool_name,
            risk="medium",
            reason=reason,
            data={
                "tool_name": tool_call.tool_name,
                "repeat_count": repeat_count,
                "arguments_json": arguments_json,
                "patterns": [arguments_json],
            },
        )
        context = _tool_context(
            session_id=session_id,
            tool_call=tool_call,
            run_id=run_id,
            run_metadata=run_metadata,
            iteration=iteration,
            resume_pending=resume_pending,
            messages=self.store.read_history(session_id),
            cancel_requested=cancel_requested,
        )
        decision = await self.tool_runtime.permission_evaluator.evaluate(
            tool_id=tool_call.tool_name,
            args=dict(tool_call.arguments or {}),
            metadata=metadata,
            context=context,
        )
        if decision.action == ASK:
            permission_request = _permission_decision_request_payload(decision.request)
            runtime_events.append(
                RuntimeEvent(
                    type="tool.permission_requested",
                    session_id=session_id,
                    message=decision.reason or reason,
                    payload={
                        **_permission_requested_payload(
                            run_id=run_id,
                            tool_call=tool_call,
                            permission_request=permission_request,
                        ),
                        "category": "doom_loop",
                        "repeat_count": repeat_count,
                    },
                )
            )
            return _ToolExecutionOutcome(
                pending_permission_request=permission_request,
            )

        if decision.action == DENY:
            message = decision.reason or "Tool execution denied."
            runtime_events.append(
                RuntimeEvent(
                    type="tool.permission_denied",
                    session_id=session_id,
                    message=message,
                    payload={
                        **_tool_event_context_payload(
                            run_id=run_id,
                            tool_call=tool_call,
                            iteration=iteration,
                        ),
                        "category": "doom_loop",
                        "repeat_count": repeat_count,
                    },
                )
            )
            self._append_tool_result(
                session_id=session_id,
                result=ToolResult(
                    call_id=tool_call.call_id,
                    tool_name=tool_call.tool_name,
                    status="permission_denied",
                    success=False,
                    error=message,
                    content=message,
                    metadata={
                        "permission_category": "doom_loop",
                        "repeat_count": repeat_count,
                        "arguments_json": arguments_json,
                    },
                ),
                runtime_events=runtime_events,
                run_id=run_id,
            )
            return _ToolExecutionOutcome()

        return None

    def _append_tool_result(
        self,
        *,
        session_id: str,
        result: ToolResult,
        runtime_events: List[RuntimeEvent],
        run_id: str,
    ) -> None:
        self._update_tool_call_state_for_result(session_id, result)
        self.store.append_message(
            session_id,
            role=MessageRole.TOOL,
            parts=[MessagePart.tool_result_part(result)],
            metadata={"tool_call_id": result.call_id},
            status="complete",
            completed_at=result.created_at,
        )
        payload = {
            "run_id": run_id,
            "tool_call_id": result.call_id,
            "tool_name": result.tool_name,
            "status": result.status,
        }
        if _is_terminal_tool_result(result):
            payload["terminal"] = True
            payload["terminal_reason"] = _terminal_reason(result)
        runtime_events.append(
            RuntimeEvent(
                type="tool_result_appended",
                session_id=session_id,
                payload=payload,
            )
        )

    def _update_tool_call_state_for_result(
        self,
        session_id: str,
        result: ToolResult,
    ) -> None:
        history = self.store.read_history(session_id)
        if not _apply_tool_result_state(history, result):
            return
        self.store.replace_history(session_id, history)


async def run_runtime_loop(
    *,
    session_id: Optional[str] = None,
    session: Optional[Session] = None,
    user_text: str,
    store: SessionStore,
    provider: Union[LLMProvider, ProviderCallable],
    adapter: Optional[LLMEventAdapter] = None,
    tool_runtime: ToolRuntime,
    max_iterations: int | None = None,
    doom_loop_threshold: Optional[int] = 3,
    default_provider_id: str = DEFAULT_PROVIDER_ID,
    default_model: str = DEFAULT_MODEL_ID,
    max_context_parts: Optional[int] = None,
    max_context_chars: Optional[int] = None,
    max_context_tokens: int | None = None,
    context_reserve_chars: int = 0,
    context_reserve_tokens: int | None = None,
    metadata: Optional[dict[str, Any]] = None,
    context_messages: Optional[list[Message]] = None,
    context_message_provider: Optional[ContextMessageProvider] = None,
    append_user_message: bool = True,
    user_parts: Optional[List[MessagePart]] = None,
    event_bus: Optional[RuntimeEventBus] = None,
    is_cancelled: Optional[CancelCallback] = None,
    tool_selection: Optional[ToolSelection] = None,
    tools: Optional[Mapping[str, bool]] = None,
    structured_output_required: bool = False,
    structured_output_tool_id: str = "StructuredOutput",
    compaction_summarizer: Optional[CompactionSummarizer] = None,
    compaction_auto: bool = True,
    compaction_rewrite_stored_history: bool = False,
    compaction_tail_turns: int = 2,
    compaction_preserve_recent_chars: int | None = None,
    compaction_preserve_recent_tokens: int | None = None,
    compaction_reserved_chars: int | None = None,
    provider_max_retries: int = 2,
    provider_retry_backoff_seconds: float = 0.0,
    provider_retry_backoff_multiplier: float = 2.0,
    enable_context_overflow_retry: bool = True,
    emit_llm_stream_events: bool = True,
    track_usage: bool = True,
    usage_pricing: Optional[Mapping[str, Any]] = None,
) -> RuntimeLoopResult:
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        adapter=adapter,
        tool_runtime=tool_runtime,
        max_iterations=max_iterations,
        doom_loop_threshold=doom_loop_threshold,
        default_provider_id=default_provider_id,
        default_model=default_model,
        max_context_parts=max_context_parts,
        max_context_chars=max_context_chars,
        max_context_tokens=max_context_tokens,
        context_reserve_chars=context_reserve_chars,
        context_reserve_tokens=context_reserve_tokens,
        event_bus=event_bus,
        is_cancelled=is_cancelled,
        tool_selection=tool_selection,
        compaction_summarizer=compaction_summarizer,
        compaction_auto=compaction_auto,
        compaction_rewrite_stored_history=compaction_rewrite_stored_history,
        compaction_tail_turns=compaction_tail_turns,
        compaction_preserve_recent_chars=compaction_preserve_recent_chars,
        compaction_preserve_recent_tokens=compaction_preserve_recent_tokens,
        compaction_reserved_chars=compaction_reserved_chars,
        provider_max_retries=provider_max_retries,
        provider_retry_backoff_seconds=provider_retry_backoff_seconds,
        provider_retry_backoff_multiplier=provider_retry_backoff_multiplier,
        enable_context_overflow_retry=enable_context_overflow_retry,
        emit_llm_stream_events=emit_llm_stream_events,
        track_usage=track_usage,
        usage_pricing=usage_pricing,
    )
    return await runner.run(
        user_text=user_text,
        session_id=session_id,
        session=session,
        metadata=metadata,
        context_messages=context_messages,
        context_message_provider=context_message_provider,
        append_user_message=append_user_message,
        user_parts=user_parts,
        tools=tools,
        structured_output_required=structured_output_required,
        structured_output_tool_id=structured_output_tool_id,
    )


async def _prefetch_async_stream(
    stream: AsyncIterable[Any],
) -> AsyncIterable[Any]:
    """Start ``stream`` and return an equivalent stream replaying its head.

    This is deliberately a plain coroutine that *returns* an async generator
    rather than an ``async def ... yield`` generator: an async generator would
    defer the first ``__anext__`` right back to the caller and the prefetch
    would be a no-op.
    """

    iterator = stream.__aiter__()
    try:
        first = await iterator.__anext__()
    except StopAsyncIteration:
        return _empty_async_stream()
    return _replay_async_stream(first, iterator)


async def _empty_async_stream() -> AsyncIterator[Any]:
    return
    yield  # pragma: no cover - unreachable, marks this an async generator


async def _replay_async_stream(
    first: Any,
    iterator: AsyncIterator[Any],
) -> AsyncIterator[Any]:
    try:
        yield first
        async for event in iterator:
            yield event
    finally:
        # `async for` never closes its iterator, and closing this wrapper does
        # not cascade into `iterator` either, so forward the close by hand:
        # otherwise abandoning the replay leaves the provider stream suspended
        # at its own ``yield`` and its cleanup waits for GC finalization. A
        # provider may hand back a plain async iterator with no ``aclose``.
        aclose = getattr(iterator, "aclose", None)
        if aclose is not None:
            await aclose()


def _last_assistant_message(messages: Iterable[Message]) -> Optional[Message]:
    for message in reversed(list(messages)):
        if message.role is MessageRole.ASSISTANT:
            return message
    return None


def _assistant_tool_calls(message: Message) -> List[ToolCall]:
    if message.role is not MessageRole.ASSISTANT:
        return []
    calls: List[ToolCall] = []
    for part in message.parts:
        if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
            calls.append(part.tool_call)
    return calls


def _recent_tool_call_repeat_count(
    messages: Iterable[Message],
    *,
    current: ToolCall,
) -> int:
    sequence = _assistant_tool_call_sequence_until(messages, current=current)
    if not sequence:
        sequence = [current]

    current_key = _tool_call_repeat_key(current)
    repeat_count = 0
    for tool_call in reversed(sequence):
        if _tool_call_repeat_key(tool_call) != current_key:
            break
        repeat_count += 1
    return repeat_count


def _assistant_tool_call_sequence_until(
    messages: Iterable[Message],
    *,
    current: ToolCall,
) -> List[ToolCall]:
    sequence: List[ToolCall] = []
    for message in messages:
        if message.role is not MessageRole.ASSISTANT:
            continue
        for part in message.parts:
            if part.type is not MessagePartType.TOOL_CALL or part.tool_call is None:
                continue
            sequence.append(part.tool_call)
            if part.tool_call.call_id == current.call_id:
                return sequence
    return [*sequence, current]


def _tool_call_repeat_key(tool_call: ToolCall) -> tuple[str, str]:
    return (tool_call.tool_name, _stable_tool_arguments_json(tool_call.arguments))


def _stable_tool_arguments_json(arguments: Optional[Mapping[str, Any]]) -> str:
    return json.dumps(
        dict(arguments or {}),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _copy_tool_selection(selection: Optional[ToolSelection]) -> ToolSelection:
    if selection is None:
        return ToolSelection()
    return ToolSelection(
        enabled=None if selection.enabled is None else set(selection.enabled),
        disabled=set(selection.disabled),
        forced_disabled=set(selection.forced_disabled),
    )


def _validate_non_negative_int(value: Any, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_optional_non_negative_int(value: Any, *, field_name: str) -> None:
    if value is None:
        return
    _validate_non_negative_int(value, field_name=field_name)


def _validate_positive_int(value: Any, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


def _validate_optional_positive_int(value: Any, *, field_name: str) -> None:
    if value is None:
        return
    _validate_positive_int(value, field_name=field_name)


def _validate_non_empty_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _disabled_tool_ids(
    all_tool_ids: Iterable[str],
    *,
    enabled_tool_ids: Iterable[str],
) -> list[str]:
    enabled = {str(tool_id) for tool_id in enabled_tool_ids}
    return sorted({str(tool_id) for tool_id in all_tool_ids}.difference(enabled))


def _record_tool_selection_metadata(
    metadata: dict[str, Any],
    *,
    enabled_tool_ids: list[str],
    disabled_tool_ids: list[str],
) -> None:
    enabled = list(enabled_tool_ids)
    disabled = list(disabled_tool_ids)
    metadata["enabled_tool_ids"] = enabled
    metadata["disabled_tool_ids"] = disabled

    tools_metadata = metadata.get("tools")
    if isinstance(tools_metadata, Mapping):
        merged_tools_metadata = dict(tools_metadata)
    else:
        merged_tools_metadata = {}
        if tools_metadata is not None:
            merged_tools_metadata["caller_value"] = tools_metadata
    merged_tools_metadata.update({"enabled": enabled, "disabled": disabled})
    metadata["tools"] = merged_tools_metadata


def _record_model_aware_tool_selection_metadata(
    metadata: dict[str, Any],
    *,
    model_aware_selection: ModelAwareToolSelection,
) -> None:
    forced_disabled = list(model_aware_selection.forced_disabled)
    metadata["model_aware_tool_selection_enabled"] = model_aware_selection.enabled
    metadata["model_aware_tool_selection_ran"] = model_aware_selection.ran
    metadata["model_aware_tool_selection_model_hint"] = (
        model_aware_selection.model_hint
    )
    metadata["model_aware_tool_selection_mode"] = model_aware_selection.mode
    metadata["model_aware_tool_selection_forced_disabled"] = forced_disabled

    selection_metadata = metadata.get("model_aware_tool_selection")
    if isinstance(selection_metadata, Mapping):
        merged_selection_metadata = dict(selection_metadata)
    else:
        merged_selection_metadata = {}
        if selection_metadata is not None:
            merged_selection_metadata["caller_value"] = selection_metadata
    merged_selection_metadata.update(
        {
            "enabled": model_aware_selection.enabled,
            "ran": model_aware_selection.ran,
            "model_hint": model_aware_selection.model_hint,
            "mode": model_aware_selection.mode,
            "forced_disabled": forced_disabled,
        }
    )
    metadata["model_aware_tool_selection"] = merged_selection_metadata


def _model_aware_tool_selection_enabled(metadata: Mapping[str, Any]) -> bool:
    if "model_aware_tool_selection_enabled" not in metadata:
        return True
    value = metadata["model_aware_tool_selection_enabled"]
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _record_usage_metadata(
    metadata: dict[str, Any],
    *,
    track_usage: bool,
    pricing_enabled: bool,
) -> None:
    resolved_pricing_enabled = bool(track_usage and pricing_enabled)
    metadata["track_usage"] = bool(track_usage)
    metadata["usage_pricing_enabled"] = resolved_pricing_enabled

    usage_metadata = metadata.get("usage_telemetry")
    if isinstance(usage_metadata, Mapping):
        merged_usage_metadata = dict(usage_metadata)
    else:
        merged_usage_metadata = {}
        if usage_metadata is not None:
            merged_usage_metadata["caller_value"] = usage_metadata
    merged_usage_metadata.update(
        {
            "track_usage": bool(track_usage),
            "pricing_enabled": resolved_pricing_enabled,
        }
    )
    metadata["usage_telemetry"] = merged_usage_metadata


def _record_iteration_limit_metadata(
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


def _record_max_steps_metadata(
    metadata: dict[str, Any],
    *,
    iteration: int,
    max_iterations: int,
) -> None:
    metadata["max_steps_reached"] = True
    metadata["tools_disabled_reason"] = "max_steps"
    metadata["max_steps_iteration"] = iteration
    metadata["max_steps_limit"] = max_iterations
    loop_metadata = metadata.get("loop")
    if isinstance(loop_metadata, Mapping):
        loop = dict(loop_metadata)
    else:
        loop = {}
        if loop_metadata is not None:
            loop["caller_value"] = loop_metadata
    loop.update(
        {
            "max_steps_reached": True,
            "tools_disabled_reason": "max_steps",
            "max_steps_iteration": iteration,
            "max_steps_limit": max_iterations,
        }
    )
    metadata["loop"] = loop


def _max_steps_reached_message(*, iteration: int, max_iterations: int) -> Message:
    metadata = {
        "source": "loop.max_steps",
        "synthetic": True,
        "max_steps_reached": True,
        "iteration": iteration,
        "max_iterations": max_iterations,
    }
    return Message(
        role=MessageRole.ASSISTANT,
        parts=[MessagePart.text_part(MAX_STEPS_REACHED_PROMPT, metadata=metadata)],
        metadata=metadata,
        status="complete",
    )


async def _observe_usage_events(
    events: Union[AsyncIterable[LLMEvent], Iterable[LLMEvent]],
    *,
    on_event: Callable[[LLMEvent], None],
) -> AsyncIterable[LLMEvent]:
    if hasattr(events, "__aiter__"):
        async for event in events:  # type: ignore[union-attr]
            on_event(event)
            yield event
        return

    for event in events:  # type: ignore[union-attr]
        on_event(event)
        yield event


def _summary_with_estimated_cost(
    summary: UsageSummary,
    pricing: Mapping[str, Any],
) -> UsageSummary:
    summary.cost_usd = estimate_cost(summary, pricing)
    return summary


def _usage_payload(summary: UsageSummary) -> dict[str, Any]:
    return summary.to_dict()


def _annotate_latest_step_usage_event(
    runtime_events: List[RuntimeEvent],
    *,
    step_usage: UsageSummary,
    iteration_usage: UsageSummary,
    run_usage: UsageSummary,
) -> None:
    if not runtime_events:
        return
    event = runtime_events[-1]
    if event.type != "llm.step_finish":
        return
    event.payload["usage_summary"] = _usage_payload(step_usage)
    event.payload["iteration_usage"] = _usage_payload(iteration_usage)
    event.payload["run_usage"] = _usage_payload(run_usage)


def _disabled_tool_result(tool_call: ToolCall) -> ToolResult:
    message = f"Tool is disabled: {tool_call.tool_name}"
    return ToolResult(
        call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        status="disabled",
        success=False,
        error=message,
        content=message,
        metadata={"disabled": True},
    )


def _noop_tool_result(tool_call: ToolCall) -> ToolResult:
    message = "Ignored provider no-op fallback tool call."
    return ToolResult(
        call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        status="ignored",
        success=False,
        error=message,
        content=message,
        metadata={"ignored": True, "noop_fallback": True},
    )


def _apply_tool_result_state(history: list[Message], result: ToolResult) -> bool:
    for message in reversed(history):
        if message.role is not MessageRole.ASSISTANT:
            continue
        for part in message.parts:
            if (
                part.type is MessagePartType.TOOL_CALL
                and part.tool_call is not None
                and part.tool_call.call_id == result.call_id
            ):
                _set_completed_tool_part_state(part, result)
                return True
    return False


def _set_completed_tool_part_state(part: MessagePart, result: ToolResult) -> None:
    previous_state = part.metadata.get("tool_state")
    previous_state = dict(previous_state) if isinstance(previous_state, Mapping) else {}
    previous_time = previous_state.get("time")
    previous_time = dict(previous_time) if isinstance(previous_time, Mapping) else {}
    status = "completed" if result.success else "error"
    part.tool_call.status = status
    state: dict[str, Any] = {
        "status": status,
        "input": previous_state.get("input", dict(part.tool_call.arguments)),
        "raw": previous_state.get("raw", part.tool_call.arguments_text),
        "input_ended": previous_state.get("input_ended", True),
        "metadata": dict(result.metadata),
        "time": {
            "start": previous_time.get("start") or part.created_at,
            "end": result.created_at,
        },
    }
    if result.success:
        state["output"] = result.output if result.output is not None else result.content
    else:
        state["error"] = result.error or result.content
    title = result.metadata.get("title")
    if title is not None:
        state["title"] = str(title)
    part.metadata["tool_state"] = state


def _is_terminal_tool_result(result: ToolResult) -> bool:
    return result.metadata.get("terminal") is True


def _terminal_reason(result: ToolResult) -> Optional[str]:
    value = result.metadata.get("terminal_reason")
    if value is None or value == "":
        return None
    return str(value)


def _structured_output_from_terminal_result(
    result: ToolResult,
) -> Optional[dict[str, Any]]:
    if _terminal_reason(result) != "structured_output":
        return None
    value = result.metadata.get("structured_output")
    if not isinstance(value, Mapping):
        return None
    return deepcopy(dict(value))


def _tool_context(
    *,
    session_id: str,
    tool_call: ToolCall,
    run_id: str,
    run_metadata: Mapping[str, Any] | None,
    iteration: Optional[int],
    resume_pending: bool,
    messages: Iterable[Message] | None = None,
    cancel_requested: Callable[[], Any] | None = None,
) -> ToolContext:
    message_list = list(messages or [])
    message_id = _message_id_for_tool_call(message_list, tool_call)
    metadata: dict[str, Any] = dict(run_metadata or {})
    if message_id is not None:
        metadata["message_id"] = message_id
    metadata["tool_call_id"] = tool_call.call_id
    metadata["tool_name"] = tool_call.tool_name
    metadata["run_id"] = run_id
    if iteration is not None:
        metadata["iteration"] = iteration
    elif resume_pending:
        metadata["iteration"] = "resume"
        metadata["resume"] = True

    return ToolContext(
        session_id=session_id,
        request_id=run_id,
        message_id=message_id,
        metadata=metadata,
        tool_call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        run_id=run_id,
        iteration=iteration,
        extra=dict(run_metadata or {}),
        messages=message_list,
        agent=_metadata_agent(run_metadata),
        cancel_requested=cancel_requested,
    )


def _message_id_for_tool_call(
    messages: Iterable[Message],
    tool_call: ToolCall,
) -> str | None:
    for message in reversed(list(messages)):
        if message.role is not MessageRole.ASSISTANT:
            continue
        for part in message.parts:
            if (
                part.type is MessagePartType.TOOL_CALL
                and part.tool_call is not None
                and part.tool_call.call_id == tool_call.call_id
            ):
                return message.message_id
    return None


def _metadata_agent(metadata: Mapping[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    for key in ("agent_name", "agent", "command_agent"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _tool_event_context_payload(
    *,
    run_id: str,
    tool_call: ToolCall,
    iteration: Optional[int],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "tool_call_id": tool_call.call_id,
        "tool_name": tool_call.tool_name,
    }
    if iteration is not None:
        payload["iteration"] = iteration
    return payload


def _fill_tool_event_context(
    event: RuntimeEvent,
    *,
    run_id: str,
    tool_call: ToolCall,
    iteration: Optional[int],
) -> None:
    payload = _tool_event_context_payload(
        run_id=run_id,
        tool_call=tool_call,
        iteration=iteration,
    )
    for key, value in payload.items():
        if event.payload.get(key) in (None, ""):
            event.payload[key] = value


def _permission_request_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_request = metadata.get("permission_request")
    if isinstance(raw_request, Mapping):
        return dict(raw_request)
    return {}


def _question_request_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_request = metadata.get("question_request")
    if isinstance(raw_request, Mapping):
        return dict(raw_request)
    return {}


def _permission_decision_request_payload(request: Any) -> dict[str, Any]:
    if request is None:
        return {}
    if hasattr(request, "to_dict"):
        payload = request.to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
        return {}
    if isinstance(request, Mapping):
        return dict(request)
    return {}


def _permission_requested_payload(
    *,
    run_id: str,
    tool_call: ToolCall,
    permission_request: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "tool_call_id": tool_call.call_id,
        "tool_name": tool_call.tool_name,
        "permission_request": dict(permission_request),
    }


def _question_requested_payload(
    *,
    run_id: str,
    tool_call: ToolCall,
    question_request: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "tool_call_id": tool_call.call_id,
        "tool_name": tool_call.tool_name,
        "question_request": dict(question_request),
    }


def _message_with_unique_part_ids(
    message: Message,
    *,
    existing_part_ids: set[str],
) -> Message:
    cloned = deepcopy(message)
    seen = set(existing_part_ids)
    for part in cloned.parts:
        if not part.part_id or part.part_id in seen:
            part.part_id = new_id("part")
        seen.add(part.part_id)
    return cloned


def _session_part_ids(messages: Iterable[Message]) -> set[str]:
    return {part.part_id for message in messages for part in message.parts}


def _request_metadata(
    metadata: Optional[dict[str, Any]],
    *,
    session_id: str,
    iteration: int,
    max_iterations: int | None,
) -> dict[str, Any]:
    request_metadata = dict(metadata or {})
    request_metadata["session_id"] = session_id
    request_metadata["iteration"] = iteration
    _record_iteration_limit_metadata(
        request_metadata,
        max_iterations=max_iterations,
    )
    loop_metadata = request_metadata.get("loop")
    if isinstance(loop_metadata, Mapping):
        merged_loop_metadata = dict(loop_metadata)
    else:
        merged_loop_metadata = {}
        if loop_metadata is not None:
            merged_loop_metadata["caller_value"] = loop_metadata
    merged_loop_metadata.update({"session_id": session_id, "iteration": iteration})
    _record_iteration_limit_metadata(
        merged_loop_metadata,
        max_iterations=max_iterations,
    )
    request_metadata["loop"] = merged_loop_metadata
    return request_metadata


def _request_context_messages(
    *,
    context_messages: Optional[list[Message]],
    context_message_provider: Optional[ContextMessageProvider],
    metadata: dict[str, Any],
    run_metadata: dict[str, Any],
) -> list[Message]:
    messages = list(context_messages or [])
    if context_message_provider is None:
        return messages

    dynamic_messages = list(context_message_provider(metadata) or [])
    _record_dynamic_instruction_context_metadata(metadata, dynamic_messages)
    _record_dynamic_instruction_context_metadata(run_metadata, dynamic_messages)
    return [*messages, *dynamic_messages]


def _record_dynamic_instruction_context_metadata(
    metadata: dict[str, Any],
    messages: Iterable[Message],
) -> None:
    instruction_messages = [
        message
        for message in messages
        if message.metadata.get("kind") == "instruction_context"
    ]
    metadata["instruction_context_count"] = len(instruction_messages)
    paths = [
        str(path)
        for message in instruction_messages
        if message.metadata.get("source") == "file"
        for path in [message.metadata.get("path")]
        if path
    ]
    if paths:
        metadata["system_instruction_paths"] = paths
    else:
        metadata.pop("system_instruction_paths", None)


def _with_model_context_compaction_metadata(
    prepared_request: Any,
    model_context_metadata: Mapping[str, Any],
):
    if not prepared_request.compaction_applied:
        return prepared_request
    request_metadata = dict(prepared_request.request.metadata)
    existing_compaction = request_metadata.get("compaction")
    if isinstance(existing_compaction, Mapping):
        compaction_metadata = dict(existing_compaction)
    else:
        compaction_metadata = {}
    compaction_metadata.update(dict(model_context_metadata))
    request_metadata["compaction"] = dict(compaction_metadata)
    provider_request = replace(
        prepared_request.request,
        metadata=request_metadata,
    )
    return replace(
        prepared_request,
        request=provider_request,
        compaction_metadata={
            **dict(prepared_request.compaction_metadata),
            **dict(model_context_metadata),
        },
    )


def _auto_compaction_operation_metadata(
    *,
    budget: ContextBudget,
    trigger: str,
    overflow: bool,
    overflow_retry: bool,
    tail_turns: int,
    preserve_recent_chars: int | None,
    tail_start_message_id: str | None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "trigger": trigger,
        "compaction_trigger": trigger,
        "auto": True,
        "overflow": overflow,
        "overflow_retry": overflow_retry,
        "max_parts": budget.max_parts,
        "max_chars": budget.max_chars,
        "reserve_chars": budget.reserve_chars,
        "tail_turns": tail_turns,
    }
    if preserve_recent_chars is not None:
        metadata["preserve_recent_chars"] = preserve_recent_chars
    if tail_start_message_id is not None:
        metadata["tail_start_message_id"] = tail_start_message_id
    return metadata


def _compaction_result_metadata(
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


def _apply_auto_compaction_metadata(
    messages: Iterable[Message],
    *,
    source_messages: Iterable[Message],
    summary: str | None,
    metadata: Mapping[str, Any],
    overflow: bool,
    tail_start_message_id: str | None,
) -> list[Message]:
    source_message_ids = {message.message_id for message in source_messages}
    marker = {
        "auto": True,
        "compaction_trigger": metadata.get("trigger", "context_budget"),
        "overflow": overflow,
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
            part.compaction.auto = True
            part.compaction.overflow = overflow
            part.compaction.tail_start_message_id = tail_start_message_id
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


def _tail_start_message_id(
    messages: Iterable[Message],
    *,
    tail_turns: int,
) -> str | None:
    if tail_turns <= 0:
        return None
    user_messages = [
        message
        for message in messages
        if message.role is MessageRole.USER
    ]
    if not user_messages:
        return None
    return user_messages[max(0, len(user_messages) - tail_turns)].message_id


def _active_user_message(
    messages: Iterable[Message],
    *,
    preferred_message_id: str | None = None,
) -> Message | None:
    message_list = list(messages)
    if preferred_message_id is not None:
        for message in reversed(message_list):
            if (
                message.message_id == preferred_message_id
                and message.role is MessageRole.USER
                and not _message_has_compaction_part(message)
            ):
                return message

    for message in reversed(message_list):
        if message.role is not MessageRole.USER:
            continue
        if _message_has_compaction_part(message):
            continue
        return message
    return None


def _ensure_active_user_replay(
    messages: Iterable[Message],
    *,
    active_user_message: Message | None,
    trigger: str,
    overflow_retry: bool,
) -> tuple[list[Message], _CompactionReplayInfo | None]:
    compacted_messages = list(messages)
    if active_user_message is None:
        return compacted_messages, None
    if _history_contains_visible_message(
        compacted_messages,
        message_id=active_user_message.message_id,
    ):
        return compacted_messages, None

    replay_message, replay_info = _build_compaction_replay_message(
        active_user_message,
        trigger=trigger,
        overflow_retry=overflow_retry,
    )
    return [*compacted_messages, replay_message], replay_info


def _history_contains_visible_message(
    messages: Iterable[Message],
    *,
    message_id: str,
) -> bool:
    for message in messages:
        if message.message_id != message_id:
            continue
        if message.role is not MessageRole.USER:
            return False
        return _message_has_model_visible_content(message)
    return False


def _message_has_model_visible_content(message: Message) -> bool:
    for part in message.parts:
        if part.type is MessagePartType.TEXT and (part.text or ""):
            return True
        if part.type is MessagePartType.ERROR and (part.text or ""):
            return True
        if part.type is MessagePartType.TASK and part.task is not None:
            if part.task.prompt:
                return True
        if part.type is MessagePartType.ATTACHMENT and part.attachment is not None:
            return True
    return False


def _message_has_compaction_part(message: Message) -> bool:
    return any(part.type is MessagePartType.COMPACTION for part in message.parts)


def _build_compaction_replay_message(
    active_user_message: Message,
    *,
    trigger: str,
    overflow_retry: bool,
) -> tuple[Message, _CompactionReplayInfo]:
    replayed_message_id = _replayed_message_id(active_user_message)
    replay_message_id = new_id("msg")
    parts = _compaction_replay_parts(
        active_user_message,
        replayed_message_id=replayed_message_id,
        trigger=trigger,
        overflow_retry=overflow_retry,
    )
    auto_continue = False
    if not parts:
        auto_continue = True
        parts = [
            _compaction_replay_text_part(
                _COMPACTION_REPLAY_CONTINUE_TEXT,
                replayed_message_id=replayed_message_id,
                trigger=trigger,
                overflow_retry=overflow_retry,
                auto_continue=True,
            )
        ]

    replay_info = _CompactionReplayInfo(
        replayed_message_id=replayed_message_id,
        replay_message_id=replay_message_id,
        compaction_trigger=trigger,
        overflow_retry=overflow_retry,
        auto_continue=auto_continue,
    )
    return (
        Message(
            role=MessageRole.USER,
            session_id=active_user_message.session_id,
            message_id=replay_message_id,
            parts=parts,
            metadata={
                "source": "compaction.replay",
                **_compaction_replay_metadata(replay_info),
            },
            status="complete",
        ),
        replay_info,
    )


def _compaction_replay_parts(
    message: Message,
    *,
    replayed_message_id: str,
    trigger: str,
    overflow_retry: bool,
) -> list[MessagePart]:
    parts: list[MessagePart] = []
    for part in message.parts:
        replay_text = _compaction_replay_part_text(part)
        if not replay_text:
            continue
        replay_part = _compaction_replay_text_part(
            replay_text,
            replayed_message_id=replayed_message_id,
            trigger=trigger,
            overflow_retry=overflow_retry,
        )
        replay_part.metadata["replayed_part_id"] = part.part_id
        replay_part.metadata["replayed_part_type"] = part.type.value
        parts.append(replay_part)
    return parts


def _compaction_replay_part_text(part: MessagePart) -> str | None:
    if part.type is MessagePartType.TEXT:
        return part.text or None
    if part.type is MessagePartType.ERROR:
        return part.text or None
    if part.type is MessagePartType.TASK and part.task is not None:
        return part.task.prompt or None
    if part.type is MessagePartType.ATTACHMENT and part.attachment is not None:
        label = (
            part.attachment.filename
            or part.attachment.mime_type
            or part.attachment.attachment_id
            or "attachment"
        )
        return f"[Attachment omitted during compaction replay: {label}]"
    return None


def _compaction_replay_text_part(
    text: str,
    *,
    replayed_message_id: str,
    trigger: str,
    overflow_retry: bool,
    auto_continue: bool = False,
) -> MessagePart:
    metadata = {
        "source": "compaction.replay",
        "compaction_replay": True,
        "compaction_trigger": trigger,
        "replayed_message_id": replayed_message_id,
    }
    if overflow_retry:
        metadata["overflow_retry"] = True
    if auto_continue:
        metadata["auto_continue"] = True
    return MessagePart.text_part(text, metadata=metadata)


def _replayed_message_id(message: Message) -> str:
    if message.metadata.get("compaction_replay") is True:
        value = message.metadata.get("replayed_message_id")
        if value:
            return str(value)
    return message.message_id


def _compaction_replay_metadata(
    replay_info: _CompactionReplayInfo,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "compaction_replay": True,
        "compaction_trigger": replay_info.compaction_trigger,
        "replayed_message_id": replay_info.replayed_message_id,
        "replay_message_id": replay_info.replay_message_id,
    }
    if replay_info.overflow_retry:
        metadata["overflow_retry"] = True
    if replay_info.auto_continue:
        metadata["auto_continue"] = True
    return metadata


def _apply_compaction_replay_request_metadata(
    metadata: dict[str, Any],
    replay_info: _CompactionReplayInfo | None,
) -> None:
    if replay_info is None:
        return
    replay_metadata = _compaction_replay_metadata(replay_info)
    metadata.update(replay_metadata)
    compaction_metadata = metadata.get("compaction")
    if isinstance(compaction_metadata, Mapping):
        compaction_payload = dict(compaction_metadata)
    else:
        compaction_payload = {}
    compaction_payload.update(replay_metadata)
    metadata["compaction"] = compaction_payload


def _append_request_compaction_event(
    runtime_events: list[RuntimeEvent],
    *,
    request: RuntimeRequest,
    session_id: str,
    run_id: str,
    iteration: int,
) -> None:
    """Report render-time compaction, which never touches stored history.

    The stored-history compactor emits session_compaction_started/session_compacted,
    but request rendering dropped messages silently. That was tolerable while a
    budget only existed for operators who configured one; now the model catalog
    supplies a budget on every run, so an unconfigured session can quietly lose
    the older two thirds of its context while the Portal transcript still shows
    it in full.

    This deliberately uses its own ``request_compacted`` type rather than
    reusing ``session_compacted``: that type means the stored session was
    rewritten, and a consumer must be able to tell whether anything on disk
    changed. Both project to the same ``session.next.compaction.ended`` UI
    event, and the projection carries ``stored``/``scope`` through so the
    distinction survives.
    """

    prepared = request.prepared_request
    if not prepared.compaction_applied:
        return
    runtime_events.append(
        RuntimeEvent(
            type="request_compacted",
            message="Request context compacted to fit the model context budget.",
            session_id=session_id,
            payload={
                "run_id": run_id,
                "iteration": iteration,
                "trigger": "context_budget",
                "auto": True,
                "stored": False,
                "scope": "request",
                **dict(prepared.compaction_metadata),
            },
        )
    )


def _apply_compaction_replay_metadata_to_request(
    request: RuntimeRequest,
    replay_info: _CompactionReplayInfo | None,
) -> None:
    replay_metadata = (
        _compaction_replay_metadata(replay_info)
        if replay_info is not None
        else _compaction_replay_payload_from_metadata(request.metadata)
    )
    if not replay_metadata:
        return

    for metadata in (
        request.metadata,
        request.provider_request.metadata,
        request.prepared_request.request.metadata,
    ):
        metadata.update(replay_metadata)
        compaction_metadata = metadata.get("compaction")
        if isinstance(compaction_metadata, Mapping):
            compaction_payload = dict(compaction_metadata)
        else:
            compaction_payload = {}
        compaction_payload.update(replay_metadata)
        metadata["compaction"] = compaction_payload
    request.prepared_request.compaction_metadata.update(replay_metadata)


def _compaction_replay_payload_from_metadata(
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    if metadata.get("compaction_replay") is not True:
        return {}
    replayed_message_id = metadata.get("replayed_message_id")
    if not replayed_message_id:
        return {}
    payload: dict[str, Any] = {
        "compaction_replay": True,
        "replayed_message_id": str(replayed_message_id),
    }
    for key in (
        "compaction_trigger",
        "replay_message_id",
        "overflow_retry",
        "auto_continue",
    ):
        value = metadata.get(key)
        if value not in (None, False, ""):
            payload[key] = value
    return payload


def _provider_retry_event(
    *,
    session_id: str,
    run_id: str,
    iteration: int,
    attempt: int,
    max_retries: int,
    delay: float,
    exc: ProviderError,
) -> RuntimeEvent:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "iteration": iteration,
        "attempt": attempt,
        "max_retries": max_retries,
        "delay_seconds": delay,
    }
    payload.update(_provider_error_info(exc))
    return RuntimeEvent(
        type="provider.retry",
        message=str(exc) or exc.__class__.__name__,
        session_id=session_id,
        payload=payload,
    )


def _with_provider_retry_metadata(
    request: RuntimeRequest,
    *,
    retry_count: int,
    max_retries: int,
    exc: ProviderError,
) -> RuntimeRequest:
    retry_metadata = _provider_retry_metadata(
        retry_count=retry_count,
        max_retries=max_retries,
        exc=exc,
    )
    runtime_metadata = dict(request.metadata)
    runtime_metadata["provider_retry"] = retry_metadata

    provider_metadata = dict(request.provider_request.metadata)
    provider_metadata["provider_retry"] = dict(retry_metadata)
    provider_request = replace(request.provider_request, metadata=provider_metadata)
    prepared_request = replace(request.prepared_request, request=provider_request)
    return replace(
        request,
        metadata=runtime_metadata,
        provider_request=provider_request,
        prepared_request=prepared_request,
    )


def _apply_provider_retry_metadata(
    request: RuntimeRequest,
    *,
    retry_count: int,
    max_retries: int,
    exc: ProviderError,
) -> None:
    retry_metadata = _provider_retry_metadata(
        retry_count=retry_count,
        max_retries=max_retries,
        exc=exc,
    )
    request.metadata["provider_retry"] = retry_metadata
    request.provider_request.metadata["provider_retry"] = dict(retry_metadata)
    request.prepared_request.request.metadata["provider_retry"] = dict(retry_metadata)


def _provider_retry_metadata(
    *,
    retry_count: int,
    max_retries: int,
    exc: ProviderError,
) -> dict[str, Any]:
    return {
        "retry_count": retry_count,
        "max_retries": max_retries,
        "last_error": _provider_error_info(exc),
    }


def _provider_error_info(exc: ProviderError) -> dict[str, Any]:
    return {
        "error_type": exc.__class__.__name__,
        "code": exc.code,
        "retryable": exc.retryable,
        "message": str(exc) or exc.__class__.__name__,
        "error_metadata": dict(exc.metadata),
    }


def _overflow_retry_metadata(
    metadata: Mapping[str, Any],
    *,
    attempt: int,
    budget: ContextBudget,
) -> dict[str, Any]:
    request_metadata = dict(metadata)
    request_metadata["overflow_retry"] = True
    request_metadata["context_overflow_retry"] = _overflow_retry_info(
        attempt=attempt,
        budget=budget,
    )
    return request_metadata


def _apply_overflow_retry_metadata(
    request: RuntimeRequest,
    *,
    attempt: int,
    budget: ContextBudget,
) -> None:
    overflow_info = _overflow_retry_info(attempt=attempt, budget=budget)
    replay_metadata = _compaction_replay_payload_from_metadata(request.metadata)
    for metadata in (
        request.metadata,
        request.provider_request.metadata,
        request.prepared_request.request.metadata,
    ):
        metadata["overflow_retry"] = True
        metadata["context_overflow_retry"] = dict(overflow_info)
        compaction_metadata = metadata.get("compaction")
        if isinstance(compaction_metadata, Mapping):
            compaction_payload = dict(compaction_metadata)
        else:
            compaction_payload = {}
        compaction_payload.update(replay_metadata)
        compaction_payload.update(overflow_info)
        metadata["compaction"] = compaction_payload
    request.prepared_request.compaction_metadata.update(replay_metadata)
    request.prepared_request.compaction_metadata.update(overflow_info)


def _overflow_retry_info(
    *,
    attempt: int,
    budget: ContextBudget,
) -> dict[str, Any]:
    return {
        "overflow_retry": True,
        "overflow": True,
        "trigger": "provider_context_overflow",
        "compaction_trigger": "provider_context_overflow",
        "overflow_attempt": attempt,
        "max_parts": budget.max_parts,
        "max_chars": budget.max_chars,
        "reserve_chars": budget.reserve_chars,
    }


def _exception_event_payload(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": str(exc) or exc.__class__.__name__}
    if isinstance(exc, ProviderError):
        payload.update(
            {
                "code": exc.code,
                "retryable": exc.retryable,
                "provider_error_metadata": dict(exc.metadata),
            }
        )
    return payload


__all__ = [
    "LoopStatus",
    "RuntimeLoopResult",
    "RuntimeLoopRunner",
    "run_runtime_loop",
]
