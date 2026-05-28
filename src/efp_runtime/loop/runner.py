"""Executable Runtime v2 loop runner."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, List, Optional, Union

from ..context.render import prepare_history_for_request
from ..event_bus import RuntimeEventBus
from ..events import RuntimeEvent
from ..llm.adapter import DefaultLLMEventAdapter, LLMEventAdapter
from ..llm.events import LLMEvent
from ..session.models import Message, MessagePart, MessagePartType, MessageRole, Session
from ..session.processor import RuntimeSession, SessionProcessor
from ..session.protocol import SessionStore
from ..session.status import RuntimeStatus
from ..tools.definition import ToolContext
from ..tools.runtime import ToolRuntime
from ..types import ToolCall, new_id
from .provider import LLMProvider, ProviderOutput, ProviderResult, RuntimeRequest


class LoopStatus:
    COMPLETED = "completed"
    ERROR = "error"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED = "cancelled"
    WAITING_FOR_PERMISSION = "waiting_for_permission"


@dataclass
class RuntimeLoopResult:
    session_id: str
    final_assistant_message: Optional[Message]
    iterations: int
    status: str
    runtime_events: List[RuntimeEvent] = field(default_factory=list)
    pending_permission_request: Optional[dict[str, Any]] = None


ProviderCallable = Callable[[RuntimeRequest], ProviderResult]
CancelCallback = Callable[..., Any]


@dataclass
class _ToolExecutionOutcome:
    cancelled: bool = False
    pending_permission_request: Optional[dict[str, Any]] = None


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
        max_context_parts: Optional[int] = None,
        event_bus: Optional[RuntimeEventBus] = None,
        is_cancelled: Optional[CancelCallback] = None,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        if max_context_parts is not None and max_context_parts < 1:
            raise ValueError("max_context_parts must be at least 1")
        self.store = store
        self.provider = provider
        self.adapter = adapter or DefaultLLMEventAdapter()
        self.tool_runtime = tool_runtime
        self.max_iterations = max_iterations
        self.max_context_parts = max_context_parts
        self.event_bus = event_bus
        self.is_cancelled = is_cancelled

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
    ) -> RuntimeLoopResult:
        iteration_limit = max_iterations if max_iterations is not None else self.max_iterations
        if iteration_limit < 1:
            raise ValueError("max_iterations must be at least 1")

        resolved_session_id = self._ensure_session(
            session_id=session_id,
            session=session,
            allow_create=append_user_message,
        )
        if append_user_message:
            user_parts = [MessagePart.text_part(user_text)] if user_text else []
            self.store.append_message(
                resolved_session_id,
                role=MessageRole.USER,
                parts=user_parts,
                metadata={"source": "loop.user"},
                status="complete",
            )

        runtime_events: List[RuntimeEvent] = _RuntimeEventLog(self.event_bus)
        final_assistant_message: Optional[Message] = None
        status = LoopStatus.COMPLETED
        iterations = 0
        pending_permission_request: Optional[dict[str, Any]] = None
        run_metadata = dict(metadata or {})
        run_id = str(run_metadata.get("run_id") or new_id("run"))
        run_metadata["run_id"] = run_id
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

            iteration = iterations + 1
            history = self.store.read_history(resolved_session_id)
            request_history = [*(context_messages or []), *history]
            tools = self.tool_runtime.registry.list()
            request_metadata = _request_metadata(
                run_metadata,
                session_id=resolved_session_id,
                iteration=iteration,
                max_iterations=iteration_limit,
            )
            prepared_request = prepare_history_for_request(
                request_history,
                tools=tools,
                metadata=request_metadata,
                max_parts=self.max_context_parts,
            )
            request = RuntimeRequest(
                session_id=resolved_session_id,
                messages=history,
                iteration=iteration,
                max_iterations=iteration_limit,
                metadata=request_metadata,
                provider_request=prepared_request.request,
                prepared_request=prepared_request,
                tools=tools,
            )
            runtime_events.append(
                RuntimeEvent(
                    type="iteration_start",
                    session_id=resolved_session_id,
                    payload={"run_id": run_id, "iteration": iteration},
                )
            )

            iterations = iteration
            try:
                provider_output = await self._invoke_provider(request)
                events = self._normalize_provider_output(provider_output)
                processor_session = RuntimeSession(
                    session_id=resolved_session_id,
                    messages=self.store.read_history(resolved_session_id),
                    runtime_events=runtime_events,
                )
                processor = SessionProcessor(processor_session)
                assistant_message = await processor.consume(events)
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
            runtime_events.append(
                RuntimeEvent(
                    type="iteration_finish",
                    session_id=resolved_session_id,
                    message_id=final_assistant_message.message_id,
                    payload={
                        "run_id": run_id,
                        "iteration": iteration,
                        "tool_call_count": len(tool_calls),
                    },
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
        runtime_events.append(
            RuntimeEvent(
                type="run_finish",
                session_id=resolved_session_id,
                message_id=(
                    final_assistant_message.message_id
                    if final_assistant_message is not None
                    else None
                ),
                payload={
                    "run_id": run_id,
                    "status": status,
                    "iterations": iterations,
                },
            )
        )
        return RuntimeLoopResult(
            session_id=resolved_session_id,
            final_assistant_message=final_assistant_message,
            iterations=iterations,
            status=status,
            runtime_events=runtime_events,
            pending_permission_request=pending_permission_request,
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
    ) -> _ToolExecutionOutcome:
        for tool_call in tool_calls:
            if await self._cancel_requested(session_id):
                return _ToolExecutionOutcome(cancelled=True)
            runtime_events.append(
                RuntimeEvent(
                    type="tool_call_start",
                    session_id=session_id,
                    payload={
                        "run_id": run_id,
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.tool_name,
                    },
                )
            )
            result = await self.tool_runtime.execute(
                tool_call,
                context=ToolContext(session_id=session_id),
            )
            permission_request = _permission_request_payload(result.metadata)
            permission_event_published = False
            for event in result.events:
                if isinstance(event, RuntimeEvent):
                    if event.session_id is None:
                        event.session_id = session_id
                    if event.type == "tool.permission_requested":
                        permission_event_published = True
                        event.payload.update(
                            _permission_requested_payload(
                                run_id=run_id,
                                tool_call=tool_call,
                                permission_request=permission_request,
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

            self.store.append_message(
                session_id,
                role=MessageRole.TOOL,
                parts=[MessagePart.tool_result_part(result)],
                metadata={"tool_call_id": result.call_id},
                status="complete",
                completed_at=result.created_at,
            )
            runtime_events.append(
                RuntimeEvent(
                    type="tool_result_appended",
                    session_id=session_id,
                    payload={
                        "run_id": run_id,
                        "tool_call_id": result.call_id,
                        "tool_name": result.tool_name,
                        "status": result.status,
                    },
                )
            )
        return _ToolExecutionOutcome()


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
    max_context_parts: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    context_messages: Optional[list[Message]] = None,
    append_user_message: bool = True,
    event_bus: Optional[RuntimeEventBus] = None,
    is_cancelled: Optional[CancelCallback] = None,
) -> RuntimeLoopResult:
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        adapter=adapter,
        tool_runtime=tool_runtime,
        max_iterations=max_iterations,
        max_context_parts=max_context_parts,
        event_bus=event_bus,
        is_cancelled=is_cancelled,
    )
    return await runner.run(
        user_text=user_text,
        session_id=session_id,
        session=session,
        metadata=metadata,
        context_messages=context_messages,
        append_user_message=append_user_message,
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


def _permission_request_payload(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_request = metadata.get("permission_request")
    if isinstance(raw_request, Mapping):
        return dict(raw_request)
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


__all__ = [
    "LoopStatus",
    "RuntimeLoopResult",
    "RuntimeLoopRunner",
    "run_runtime_loop",
]
