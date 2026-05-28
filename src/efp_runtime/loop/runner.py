"""Executable Runtime v2 loop runner."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
import inspect
import json
from typing import Any, Callable, List, Optional, Union

from ..compaction.controller import CompactionController, CompactionSummarizer
from ..compaction.strategy import ContextBudget
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
from ..permissions import ASK, DENY, PermissionMetadata
from ..session.models import Message, MessagePart, MessagePartType, MessageRole, Session
from ..session.processor import RuntimeSession, SessionProcessor
from ..session.protocol import SessionStore
from ..session.status import RuntimeStatus
from ..tools.definition import ToolContext
from ..tools.runtime import ToolRuntime
from ..tools.selection import ToolSelection, resolve_tool_selection
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
    """Small iterative Runtime v2 orchestrator."""

    def __init__(
        self,
        *,
        store: SessionStore,
        provider: Union[LLMProvider, ProviderCallable],
        adapter: Optional[LLMEventAdapter] = None,
        tool_runtime: ToolRuntime,
        max_iterations: int = 4,
        doom_loop_threshold: Optional[int] = 3,
        max_context_parts: Optional[int] = None,
        max_context_chars: Optional[int] = None,
        context_reserve_chars: int = 0,
        event_bus: Optional[RuntimeEventBus] = None,
        is_cancelled: Optional[CancelCallback] = None,
        tool_selection: Optional[ToolSelection] = None,
        compaction_summarizer: Optional[CompactionSummarizer] = None,
        provider_max_retries: int = 2,
        provider_retry_backoff_seconds: float = 0.0,
        provider_retry_backoff_multiplier: float = 2.0,
        enable_context_overflow_retry: bool = True,
        emit_llm_stream_events: bool = True,
        track_usage: bool = True,
        usage_pricing: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if doom_loop_threshold is not None and doom_loop_threshold < 2:
            raise ValueError("doom_loop_threshold must be at least 2 or None")
        if max_context_parts is not None and max_context_parts < 1:
            raise ValueError("max_context_parts must be at least 1")
        if max_context_chars is not None and max_context_chars < 1:
            raise ValueError("max_context_chars must be at least 1")
        if context_reserve_chars < 0:
            raise ValueError("context_reserve_chars must be at least 0")
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
        self.max_context_parts = max_context_parts
        self.max_context_chars = max_context_chars
        self.context_reserve_chars = context_reserve_chars
        self.event_bus = event_bus
        self.is_cancelled = is_cancelled
        self.tool_selection = _copy_tool_selection(tool_selection)
        self.compaction_summarizer = compaction_summarizer
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
        append_user_message: bool = True,
        user_parts: Optional[List[MessagePart]] = None,
        tools: Optional[Mapping[str, bool]] = None,
        structured_output_required: bool = False,
        structured_output_tool_id: str = "StructuredOutput",
    ) -> RuntimeLoopResult:
        iteration_limit = max_iterations if max_iterations is not None else self.max_iterations
        if iteration_limit < 1:
            raise ValueError("max_iterations must be at least 1")

        all_tool_ids = self.tool_runtime.registry.ids()
        enabled_tool_ids = resolve_tool_selection(
            all_tool_ids,
            enabled=self.tool_selection.enabled,
            disabled=self.tool_selection.disabled,
            forced_disabled=self.tool_selection.forced_disabled,
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
        if append_user_message:
            if user_parts is not None:
                resolved_user_parts = list(user_parts)
            elif user_text:
                resolved_user_parts = [MessagePart.text_part(user_text)]
            else:
                resolved_user_parts = []
            self.store.append_message(
                resolved_session_id,
                role=MessageRole.USER,
                parts=resolved_user_parts,
                metadata={"source": "loop.user"},
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
        run_metadata = dict(metadata or {})
        run_id = str(run_metadata.get("run_id") or new_id("run"))
        run_metadata["run_id"] = run_id
        run_metadata["emit_llm_stream_events"] = self.emit_llm_stream_events
        _record_usage_metadata(
            run_metadata,
            track_usage=self.track_usage,
            pricing_enabled=bool(self.usage_pricing),
        )
        _record_tool_selection_metadata(
            run_metadata,
            enabled_tool_ids=enabled_tool_ids,
            disabled_tool_ids=disabled_tool_ids,
        )
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

        runtime_events.append(
            RuntimeEvent(
                type="run_start",
                session_id=resolved_session_id,
                payload={
                    "run_id": run_id,
                    "max_iterations": iteration_limit,
                    "enabled_tool_ids": list(enabled_tool_ids),
                    "disabled_tool_ids": list(disabled_tool_ids),
                    "emit_llm_stream_events": self.emit_llm_stream_events,
                    "track_usage": self.track_usage,
                    "usage_pricing_enabled": bool(
                        self.track_usage and self.usage_pricing
                    ),
                },
            )
        )

        while iterations < iteration_limit:
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
            request_history = [*(context_messages or []), *history]
            request_metadata = _request_metadata(
                run_metadata,
                session_id=resolved_session_id,
                iteration=iteration,
                max_iterations=iteration_limit,
            )
            request = await self._prepare_runtime_request(
                session_id=resolved_session_id,
                history=history,
                request_history=request_history,
                iteration=iteration,
                max_iterations=iteration_limit,
                metadata=request_metadata,
                tools=enabled_tools,
                budget=self._context_budget(),
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
                    request_history=request_history,
                    tools=enabled_tools,
                    runtime_events=runtime_events,
                    run_id=run_id,
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

            if iterations >= iteration_limit:
                status = LoopStatus.MAX_ITERATIONS
                runtime_events.append(
                    RuntimeEvent(
                        type="loop.max_iterations",
                        message="Maximum loop iterations reached.",
                        session_id=resolved_session_id,
                        message_id=final_assistant_message.message_id,
                        payload={
                            "run_id": run_id,
                            "max_iterations": iteration_limit,
                            "pending_tool_call_count": len(tool_calls),
                        },
                    )
                )
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

    def _context_budget(self) -> ContextBudget:
        return ContextBudget(
            max_parts=self.max_context_parts,
            max_chars=self.max_context_chars,
            reserve_chars=self.context_reserve_chars,
        )

    def _context_budget_enabled(self, budget: Optional[ContextBudget] = None) -> bool:
        resolved = budget or self._context_budget()
        return resolved.max_parts is not None or resolved.max_chars is not None

    def _overflow_retry_budget(self) -> ContextBudget:
        max_parts = self.max_context_parts
        max_chars = self.max_context_chars
        if max_parts is not None:
            max_parts = max(1, min(max_parts, 2))
        if max_chars is not None:
            max_chars = max(1, max_chars // 2)
        if max_parts is None and max_chars is None:
            max_parts = 8
        return ContextBudget(
            max_parts=max_parts,
            max_chars=max_chars,
            reserve_chars=self.context_reserve_chars,
        )

    async def _prepare_runtime_request(
        self,
        *,
        session_id: str,
        history: list[Message],
        request_history: list[Message],
        iteration: int,
        max_iterations: int,
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
        return raw_result

    async def _invoke_provider_with_retries(
        self,
        request: RuntimeRequest,
        *,
        history: list[Message],
        request_history: list[Message],
        tools: list[Any],
        runtime_events: List[RuntimeEvent],
        run_id: str,
        iteration: int,
        max_iterations: int,
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
                overflow_budget = self._overflow_retry_budget()
                overflow_metadata = _overflow_retry_metadata(
                    current_request.metadata,
                    attempt=overflow_retry_count,
                    budget=overflow_budget,
                )
                overflow_request = await self._prepare_runtime_request(
                    session_id=current_request.session_id,
                    history=history,
                    request_history=request_history,
                    iteration=iteration,
                    max_iterations=max_iterations,
                    metadata=overflow_metadata,
                    tools=tools,
                    budget=overflow_budget,
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
                runtime_events.append(
                    RuntimeEvent(
                        type="provider.context_overflow_retry",
                        message="Provider context overflow triggered compacted retry.",
                        session_id=current_request.session_id,
                        payload={
                            "run_id": run_id,
                            "iteration": iteration,
                            "attempt": overflow_retry_count,
                            "error_type": exc.__class__.__name__,
                            "code": exc.code,
                            "compaction": dict(
                                overflow_request.prepared_request.compaction_metadata
                            ),
                        },
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


async def run_runtime_loop(
    *,
    session_id: Optional[str] = None,
    session: Optional[Session] = None,
    user_text: str,
    store: SessionStore,
    provider: Union[LLMProvider, ProviderCallable],
    adapter: Optional[LLMEventAdapter] = None,
    tool_runtime: ToolRuntime,
    max_iterations: int = 4,
    doom_loop_threshold: Optional[int] = 3,
    max_context_parts: Optional[int] = None,
    max_context_chars: Optional[int] = None,
    context_reserve_chars: int = 0,
    metadata: Optional[dict[str, Any]] = None,
    context_messages: Optional[list[Message]] = None,
    append_user_message: bool = True,
    user_parts: Optional[List[MessagePart]] = None,
    event_bus: Optional[RuntimeEventBus] = None,
    is_cancelled: Optional[CancelCallback] = None,
    tool_selection: Optional[ToolSelection] = None,
    tools: Optional[Mapping[str, bool]] = None,
    structured_output_required: bool = False,
    structured_output_tool_id: str = "StructuredOutput",
    compaction_summarizer: Optional[CompactionSummarizer] = None,
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
        max_context_parts=max_context_parts,
        max_context_chars=max_context_chars,
        context_reserve_chars=context_reserve_chars,
        event_bus=event_bus,
        is_cancelled=is_cancelled,
        tool_selection=tool_selection,
        compaction_summarizer=compaction_summarizer,
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
        append_user_message=append_user_message,
        user_parts=user_parts,
        tools=tools,
        structured_output_required=structured_output_required,
        structured_output_tool_id=structured_output_tool_id,
    )


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
) -> ToolContext:
    metadata: dict[str, Any] = dict(run_metadata or {})
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
        metadata=metadata,
        tool_call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        run_id=run_id,
        iteration=iteration,
    )


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
    max_iterations: int,
) -> dict[str, Any]:
    request_metadata = dict(metadata or {})
    request_metadata["session_id"] = session_id
    request_metadata["iteration"] = iteration
    request_metadata["max_iterations"] = max_iterations
    loop_metadata = request_metadata.get("loop")
    if isinstance(loop_metadata, Mapping):
        merged_loop_metadata = dict(loop_metadata)
    else:
        merged_loop_metadata = {}
        if loop_metadata is not None:
            merged_loop_metadata["caller_value"] = loop_metadata
    merged_loop_metadata.update(
        {
            "session_id": session_id,
            "iteration": iteration,
            "max_iterations": max_iterations,
        }
    )
    request_metadata["loop"] = merged_loop_metadata
    return request_metadata


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
        compaction_payload.update(overflow_info)
        metadata["compaction"] = compaction_payload
    request.prepared_request.compaction_metadata.update(overflow_info)


def _overflow_retry_info(
    *,
    attempt: int,
    budget: ContextBudget,
) -> dict[str, Any]:
    return {
        "overflow_retry": True,
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
