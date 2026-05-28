"""Executable Runtime v2 loop runner."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable, List, Optional, Union

from ..events import RuntimeEvent
from ..llm.adapter import DefaultLLMEventAdapter, LLMEventAdapter
from ..llm.events import LLMEvent
from ..session.models import Message, MessagePart, MessagePartType, MessageRole, Session
from ..session.processor import RuntimeSession, SessionProcessor
from ..session.status import RuntimeStatus
from ..session.store import InMemorySessionStore
from ..tools.definition import ToolContext
from ..tools.runtime import ToolRuntime
from ..types import ToolCall, new_id
from .provider import LLMProvider, ProviderOutput, ProviderResult, RuntimeRequest


class LoopStatus:
    COMPLETED = "completed"
    ERROR = "error"
    MAX_ITERATIONS = "max_iterations"


@dataclass
class RuntimeLoopResult:
    session_id: str
    final_assistant_message: Optional[Message]
    iterations: int
    status: str
    runtime_events: List[RuntimeEvent] = field(default_factory=list)


ProviderCallable = Callable[[RuntimeRequest], ProviderResult]


class RuntimeLoopRunner:
    """Small iterative Runtime v2 orchestrator."""

    def __init__(
        self,
        *,
        store: InMemorySessionStore,
        provider: Union[LLMProvider, ProviderCallable],
        adapter: Optional[LLMEventAdapter] = None,
        tool_runtime: ToolRuntime,
        max_iterations: int = 4,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be at least 1")
        self.store = store
        self.provider = provider
        self.adapter = adapter or DefaultLLMEventAdapter()
        self.tool_runtime = tool_runtime
        self.max_iterations = max_iterations

    async def run(
        self,
        *,
        user_text: str,
        session_id: Optional[str] = None,
        session: Optional[Session] = None,
        max_iterations: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> RuntimeLoopResult:
        iteration_limit = max_iterations if max_iterations is not None else self.max_iterations
        if iteration_limit < 1:
            raise ValueError("max_iterations must be at least 1")

        resolved_session_id = self._ensure_session(session_id=session_id, session=session)
        user_parts = [MessagePart.text_part(user_text)] if user_text else []
        self.store.append_message(
            resolved_session_id,
            role=MessageRole.USER,
            parts=user_parts,
            metadata={"source": "loop.user"},
            status="complete",
        )

        runtime_events: List[RuntimeEvent] = []
        final_assistant_message: Optional[Message] = None
        status = LoopStatus.COMPLETED
        iterations = 0

        while iterations < iteration_limit:
            iteration = iterations + 1
            request = RuntimeRequest(
                session_id=resolved_session_id,
                messages=self.store.read_history(resolved_session_id),
                iteration=iteration,
                max_iterations=iteration_limit,
                metadata=dict(metadata or {}),
            )
            runtime_events.append(
                RuntimeEvent(
                    type="loop.iteration_start",
                    session_id=resolved_session_id,
                    payload={"iteration": iteration},
                )
            )

            provider_output = await self._invoke_provider(request)
            events = self._normalize_provider_output(provider_output)
            processor_session = RuntimeSession(
                session_id=resolved_session_id,
                messages=self.store.read_history(resolved_session_id),
                runtime_events=runtime_events,
            )
            processor = SessionProcessor(processor_session)
            assistant_message = await processor.consume(events)
            iterations = iteration

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
                    type="loop.iteration_finish",
                    session_id=resolved_session_id,
                    message_id=final_assistant_message.message_id,
                    payload={
                        "iteration": iteration,
                        "tool_call_count": len(tool_calls),
                    },
                )
            )

            if processor.session.status is RuntimeStatus.ERROR:
                status = LoopStatus.ERROR
                break
            if not tool_calls:
                status = LoopStatus.COMPLETED
                break

            await self._execute_tool_calls(
                session_id=resolved_session_id,
                tool_calls=tool_calls,
                runtime_events=runtime_events,
            )

            if iterations >= iteration_limit:
                status = LoopStatus.MAX_ITERATIONS
                runtime_events.append(
                    RuntimeEvent(
                        type="loop.max_iterations",
                        message="Maximum loop iterations reached.",
                        session_id=resolved_session_id,
                        message_id=final_assistant_message.message_id,
                        payload={
                            "max_iterations": iteration_limit,
                            "pending_tool_call_count": len(tool_calls),
                        },
                    )
                )
                break

        return RuntimeLoopResult(
            session_id=resolved_session_id,
            final_assistant_message=final_assistant_message,
            iterations=iterations,
            status=status,
            runtime_events=runtime_events,
        )

    def _ensure_session(
        self,
        *,
        session_id: Optional[str],
        session: Optional[Session],
    ) -> str:
        if session is not None and session_id is not None and session.session_id != session_id:
            raise ValueError("session_id does not match session.session_id")

        resolved_session_id = session.session_id if session is not None else session_id
        if resolved_session_id is None:
            return self.store.create_session().session_id

        try:
            self.store.get_session(resolved_session_id)
            return resolved_session_id
        except KeyError:
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

    async def _execute_tool_calls(
        self,
        *,
        session_id: str,
        tool_calls: List[ToolCall],
        runtime_events: List[RuntimeEvent],
    ) -> None:
        for tool_call in tool_calls:
            runtime_events.append(
                RuntimeEvent(
                    type="loop.tool_call_start",
                    session_id=session_id,
                    payload={
                        "tool_call_id": tool_call.call_id,
                        "tool_name": tool_call.tool_name,
                    },
                )
            )
            result = await self.tool_runtime.execute(
                tool_call,
                context=ToolContext(session_id=session_id),
            )
            for event in result.events:
                if isinstance(event, RuntimeEvent):
                    if event.session_id is None:
                        event.session_id = session_id
                    runtime_events.append(event)
                else:
                    runtime_events.append(
                        RuntimeEvent(
                            type="tool.event",
                            session_id=session_id,
                            payload={"event": event},
                        )
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
                    type="loop.tool_result_appended",
                    session_id=session_id,
                    payload={
                        "tool_call_id": result.call_id,
                        "tool_name": result.tool_name,
                        "status": result.status,
                    },
                )
            )


async def run_runtime_loop(
    *,
    session_id: Optional[str] = None,
    session: Optional[Session] = None,
    user_text: str,
    store: InMemorySessionStore,
    provider: Union[LLMProvider, ProviderCallable],
    adapter: Optional[LLMEventAdapter] = None,
    tool_runtime: ToolRuntime,
    max_iterations: int = 4,
    metadata: Optional[dict[str, Any]] = None,
) -> RuntimeLoopResult:
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        adapter=adapter,
        tool_runtime=tool_runtime,
        max_iterations=max_iterations,
    )
    return await runner.run(
        user_text=user_text,
        session_id=session_id,
        session=session,
        metadata=metadata,
    )


def _assistant_tool_calls(message: Message) -> List[ToolCall]:
    if message.role is not MessageRole.ASSISTANT:
        return []
    calls: List[ToolCall] = []
    for part in message.parts:
        if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
            calls.append(part.tool_call)
    return calls


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


__all__ = [
    "LoopStatus",
    "RuntimeLoopResult",
    "RuntimeLoopRunner",
    "run_runtime_loop",
]
