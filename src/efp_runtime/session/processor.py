"""Session event processor for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Union

from ..events import RuntimeEvent
from ..llm.events import LLMEvent, LLMEventType, coerce_event_type
from ..types import ToolCall, new_id, utc_now_iso
from .models import Message, MessagePart, MessagePartType, MessageRole
from .retry import NoRetryPolicy, RetryPolicy
from .status import RuntimeStatus


@dataclass
class RuntimeSession:
    session_id: str
    messages: List[Message] = field(default_factory=list)
    status: RuntimeStatus = RuntimeStatus.IDLE
    runtime_events: List[RuntimeEvent] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _ToolInputDraft:
    call_id: str
    name: str = ""
    chunks: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def arguments_text(self) -> str:
        return "".join(self.chunks)


class SessionProcessor:
    """Consume normalized LLM events into structured session messages."""

    def __init__(
        self,
        session: Optional[RuntimeSession] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self.session = session or RuntimeSession(session_id="runtime")
        self.retry_policy = retry_policy or NoRetryPolicy()
        self.current_message: Optional[Message] = None
        self.last_assistant_message: Optional[Message] = None
        self._text_buffers: Dict[str, List[str]] = {}
        self._active_text_part_id: Optional[str] = None
        self._reasoning_part: Optional[MessagePart] = None
        self._tool_inputs: Dict[str, _ToolInputDraft] = {}

    async def consume(
        self,
        events: Union[AsyncIterable[LLMEvent], Iterable[LLMEvent]],
    ) -> Optional[Message]:
        async for event in _aiter_events(events):
            await self.process_event(event)
        self._finalize_open_text_parts()
        return self.last_assistant_message

    async def process_event(self, event: LLMEvent) -> None:
        event_type = coerce_event_type(event.type)

        if event_type == LLMEventType.STEP_START:
            self.session.status = RuntimeStatus.RUNNING
            self._record_runtime_event("llm.step_start", event)
            return

        if event_type == LLMEventType.MESSAGE_START:
            self._start_assistant_message(event.message_id)
            return

        if event_type == LLMEventType.TEXT_START:
            self._ensure_assistant_message(event.message_id)
            self._start_text_part(event.part_id)
            return

        if event_type == LLMEventType.TEXT_DELTA:
            self._ensure_assistant_message(event.message_id)
            self._append_text_delta(event.part_id, event.delta or event.text)
            return

        if event_type == LLMEventType.TEXT_END:
            self._ensure_assistant_message(event.message_id)
            self._finalize_text_part(event.part_id, event.text)
            return

        if event_type == LLMEventType.REASONING_DELTA:
            self._ensure_assistant_message(event.message_id)
            self._append_reasoning_delta(event.delta or event.text)
            return

        if event_type == LLMEventType.TOOL_INPUT_START:
            self._ensure_assistant_message(event.message_id)
            self._finalize_open_text_parts()
            self._start_tool_input(event)
            return

        if event_type == LLMEventType.TOOL_INPUT_DELTA:
            self._ensure_assistant_message(event.message_id)
            self._append_tool_input_delta(event)
            return

        if event_type == LLMEventType.TOOL_INPUT_END:
            self._ensure_assistant_message(event.message_id)
            self._finish_tool_input(event)
            return

        if event_type == LLMEventType.TOOL_CALL_COMPLETE:
            self._ensure_assistant_message(event.message_id)
            self._append_tool_call(event)
            self.session.status = RuntimeStatus.WAITING_TOOL
            return

        if event_type == LLMEventType.TOOL_RESULT:
            self._append_tool_result(event)
            return

        if event_type == LLMEventType.STEP_FINISH:
            self._finalize_open_text_parts()
            assistant_message = self.last_assistant_message
            if assistant_message is not None:
                if assistant_message.status != "error":
                    assistant_message.status = "complete"
                assistant_message.usage.update(event.usage)
                if assistant_message.completed_at is None:
                    assistant_message.completed_at = utc_now_iso()
            if self.session.status is not RuntimeStatus.ERROR:
                self.session.status = RuntimeStatus.FINISHED
            self._record_runtime_event("llm.step_finish", event)
            return

        if event_type == LLMEventType.ERROR:
            message = self._ensure_assistant_message(event.message_id)
            message.append_part(
                MessagePart.error_part(
                    event.error or event.text,
                    metadata=dict(event.metadata),
                )
            )
            message.status = "error"
            self.session.status = RuntimeStatus.ERROR
            self._record_runtime_event("llm.error", event)
            return

    def _start_assistant_message(self, message_id: Optional[str]) -> Message:
        message = Message(
            role=MessageRole.ASSISTANT,
            session_id=self.session.session_id,
            message_id=message_id or new_id("msg"),
            status="running",
        )
        self.session.messages.append(message)
        self.current_message = message
        self.last_assistant_message = message
        self._reasoning_part = None
        return message

    def _ensure_assistant_message(self, message_id: Optional[str] = None) -> Message:
        if self.current_message is None or self.current_message.role is not MessageRole.ASSISTANT:
            return self._start_assistant_message(message_id)
        return self.current_message

    def _start_text_part(self, part_id: Optional[str]) -> None:
        resolved_part_id = part_id or "text_0"
        self._active_text_part_id = resolved_part_id
        self._text_buffers.setdefault(resolved_part_id, [])

    def _append_text_delta(self, part_id: Optional[str], delta: str) -> None:
        if not delta:
            return
        resolved_part_id = part_id or self._active_text_part_id or "text_0"
        self._active_text_part_id = resolved_part_id
        self._text_buffers.setdefault(resolved_part_id, []).append(delta)

    def _finalize_text_part(self, part_id: Optional[str], text: str = "") -> None:
        resolved_part_id = part_id or self._active_text_part_id
        if resolved_part_id is None:
            return
        buffered = "".join(self._text_buffers.pop(resolved_part_id, []))
        final_text = text or buffered
        if final_text and self.current_message is not None:
            self.current_message.append_part(
                MessagePart.text_part(final_text, part_id=resolved_part_id)
            )
        if self._active_text_part_id == resolved_part_id:
            self._active_text_part_id = None

    def _finalize_open_text_parts(self) -> None:
        for part_id in list(self._text_buffers):
            self._finalize_text_part(part_id)

    def _append_reasoning_delta(self, delta: str) -> None:
        if not delta or self.current_message is None:
            return
        if self._reasoning_part is None:
            self._reasoning_part = self.current_message.append_part(
                MessagePart(
                    type=MessagePartType.REASONING,
                    reasoning="",
                    text="",
                )
            )
        self._reasoning_part.reasoning = (self._reasoning_part.reasoning or "") + delta
        self._reasoning_part.text = (self._reasoning_part.text or "") + delta

    def _start_tool_input(self, event: LLMEvent) -> None:
        call_id = event.tool_call_id or f"call_{len(self._tool_inputs)}"
        self._tool_inputs[call_id] = _ToolInputDraft(
            call_id=call_id,
            name=event.tool_name or "",
            raw=dict(event.raw),
        )

    def _append_tool_input_delta(self, event: LLMEvent) -> None:
        call_id = event.tool_call_id or f"call_{len(self._tool_inputs)}"
        draft = self._tool_inputs.setdefault(
            call_id,
            _ToolInputDraft(call_id=call_id, name=event.tool_name or ""),
        )
        if event.tool_name and not draft.name:
            draft.name = event.tool_name
        if event.delta or event.text:
            draft.chunks.append(event.delta or event.text)
        draft.raw.update(dict(event.raw))

    def _finish_tool_input(self, event: LLMEvent) -> None:
        call_id = event.tool_call_id or f"call_{len(self._tool_inputs)}"
        draft = self._tool_inputs.setdefault(
            call_id,
            _ToolInputDraft(call_id=call_id, name=event.tool_name or ""),
        )
        if event.tool_name and not draft.name:
            draft.name = event.tool_name
        if event.text and not draft.chunks:
            draft.chunks.append(event.text)
        draft.raw.update(dict(event.raw))

    def _append_tool_call(self, event: LLMEvent) -> None:
        if self.current_message is None:
            return
        tool_call = event.tool_call or self._tool_call_from_draft(event)
        self.current_message.append_part(
            MessagePart.tool_call_part(tool_call, metadata=dict(event.metadata))
        )
        self._tool_inputs.pop(tool_call.call_id, None)

    def _tool_call_from_draft(self, event: LLMEvent) -> ToolCall:
        call_id = event.tool_call_id or f"call_{len(self._tool_inputs)}"
        draft = self._tool_inputs.get(call_id)
        name = event.tool_name or (draft.name if draft else "")
        arguments_text = draft.arguments_text if draft else event.text
        arguments = _parse_arguments(arguments_text)
        normalized_arguments = arguments if isinstance(arguments, dict) else {"value": arguments}
        return ToolCall(
            call_id=call_id,
            tool_name=name,
            arguments=normalized_arguments,
            arguments_text=arguments_text,
            raw=dict(draft.raw) if draft else dict(event.raw),
        )

    def _append_tool_result(self, event: LLMEvent) -> None:
        if event.tool_result is None:
            return
        message = Message(
            role=MessageRole.TOOL,
            session_id=self.session.session_id,
            metadata={"tool_call_id": event.tool_result.call_id},
            status="complete",
            completed_at=utc_now_iso(),
        )
        message.append_part(MessagePart.tool_result_part(event.tool_result))
        self.session.messages.append(message)

    def _record_runtime_event(self, event_type: str, event: LLMEvent) -> None:
        self.session.runtime_events.append(
            RuntimeEvent(
                type=event_type,
                session_id=self.session.session_id,
                message_id=event.message_id,
                payload={
                    "tool_call_id": event.tool_call_id,
                    "usage": dict(event.usage),
                    "error": event.error,
                    **dict(event.metadata),
                },
            )
        )


async def _aiter_events(
    events: Union[AsyncIterable[LLMEvent], Iterable[LLMEvent]],
):
    if hasattr(events, "__aiter__"):
        async for event in events:  # type: ignore[union-attr]
            yield event
        return
    for event in events:  # type: ignore[union-attr]
        yield event


def _parse_arguments(arguments_text: str) -> Any:
    if not arguments_text:
        return {}
    try:
        return json.loads(arguments_text)
    except json.JSONDecodeError:
        return arguments_text
