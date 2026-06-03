"""Session event processor for EFP runtime."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Mapping, Optional, Union

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
    input_ended: bool = False

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
        self._reasoning_parts: Dict[str, MessagePart] = {}
        self._active_reasoning_part_id: Optional[str] = None
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

        if event_type == LLMEventType.REASONING_START:
            self._ensure_assistant_message(event.message_id)
            self._start_reasoning_part(event)
            return

        if event_type == LLMEventType.REASONING_DELTA:
            self._ensure_assistant_message(event.message_id)
            self._append_reasoning_delta(event)
            return

        if event_type == LLMEventType.REASONING_END:
            self._ensure_assistant_message(event.message_id)
            self._end_reasoning_part(event)
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

        if event_type == LLMEventType.TOOL_ERROR:
            self._handle_tool_error(event)
            self._record_runtime_event("llm.tool_error", event)
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

        if event_type == LLMEventType.FINISH:
            self._record_runtime_event("llm.finish", event)
            return

        if event_type == LLMEventType.PROVIDER_ERROR:
            self._handle_provider_error(event)
            return

        if event_type == LLMEventType.ERROR:
            message = self._ensure_assistant_message(event.message_id)
            message.append_part(
                MessagePart.error_part(
                    event.error or event.text,
                    metadata=self._event_metadata(event),
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
        self._reasoning_parts = {}
        self._active_reasoning_part_id = None
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

    def _start_reasoning_part(self, event: LLMEvent) -> MessagePart | None:
        if self.current_message is None:
            return None
        part_id = self._reasoning_part_id(event)
        existing = self._reasoning_parts.get(part_id)
        if existing is not None:
            existing.metadata.update(self._event_metadata(event))
            self._reasoning_part = existing
            self._active_reasoning_part_id = part_id
            return existing
        part = self.current_message.append_part(
            MessagePart(
                type=MessagePartType.REASONING,
                part_id=part_id,
                reasoning="",
                text="",
                metadata=self._event_metadata(event),
            )
        )
        self._reasoning_parts[part_id] = part
        self._reasoning_part = part
        self._active_reasoning_part_id = part_id
        return part

    def _append_reasoning_delta(self, event: LLMEvent) -> None:
        delta = event.delta or event.text
        if not delta or self.current_message is None:
            return
        part_id = self._reasoning_part_id(event)
        part = self._reasoning_parts.get(part_id)
        if part is None:
            part = self._start_reasoning_part(event)
        if part is None:
            return
        part.metadata.update(self._event_metadata(event))
        part.reasoning = (part.reasoning or "") + delta
        part.text = (part.text or "") + delta
        self._reasoning_part = part
        self._active_reasoning_part_id = part_id

    def _end_reasoning_part(self, event: LLMEvent) -> None:
        part_id = self._reasoning_part_id(event)
        part = self._reasoning_parts.get(part_id)
        if part is None:
            return
        part.metadata.update(self._event_metadata(event))
        if self._active_reasoning_part_id == part_id:
            self._active_reasoning_part_id = None
            self._reasoning_part = None

    def _reasoning_part_id(self, event: LLMEvent) -> str:
        return (
            event.part_id
            or _optional_metadata_str(event.metadata, "part_id")
            or _optional_metadata_str(event.metadata, "reasoning_id")
            or _optional_metadata_str(event.metadata, "item_id")
            or self._active_reasoning_part_id
            or "reasoning_0"
        )

    def _event_metadata(self, event: LLMEvent) -> Dict[str, Any]:
        metadata = dict(event.metadata)
        if event.provider_metadata:
            metadata["provider_metadata"] = dict(event.provider_metadata)
        return metadata

    def _start_tool_input(self, event: LLMEvent) -> None:
        call_id = event.tool_call_id or f"call_{len(self._tool_inputs)}"
        draft = self._tool_inputs[call_id] = _ToolInputDraft(
            call_id=call_id,
            name=event.tool_name or "",
            raw=dict(event.raw),
        )
        self._ensure_pending_tool_call_part(event, draft)

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
        self._update_pending_tool_input_state(event, draft)

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
        draft.input_ended = True
        self._update_pending_tool_input_state(event, draft)

    def _append_tool_call(self, event: LLMEvent) -> None:
        if self.current_message is None:
            return
        tool_call = event.tool_call or self._tool_call_from_draft(event)
        tool_call.status = "running"
        part = self._find_current_tool_call_part(tool_call.call_id)
        if part is None:
            part = self.current_message.append_part(
                MessagePart.tool_call_part(tool_call, metadata=dict(event.metadata))
            )
        else:
            part.tool_call = tool_call
            part.metadata.update(dict(event.metadata))
        _set_tool_part_state(
            part,
            {
                "status": "running",
                "input": dict(tool_call.arguments),
                "raw": self._tool_input_raw(tool_call),
                "input_ended": self._tool_input_ended(tool_call),
                "time": {"start": utc_now_iso()},
            },
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
        self._update_tool_call_part_from_result(event.tool_result)
        message = Message(
            role=MessageRole.TOOL,
            session_id=self.session.session_id,
            metadata={"tool_call_id": event.tool_result.call_id},
            status="complete",
            completed_at=utc_now_iso(),
        )
        message.append_part(MessagePart.tool_result_part(event.tool_result))
        self.session.messages.append(message)

    def _handle_tool_error(self, event: LLMEvent) -> None:
        call_id = event.tool_call_id or ""
        if not call_id:
            return
        part = self._find_tool_call_part(call_id)
        if part is None or part.tool_call is None:
            return
        part.tool_call.status = "error"
        previous_state = part.metadata.get("tool_state")
        previous_state = dict(previous_state) if isinstance(previous_state, dict) else {}
        previous_time = previous_state.get("time")
        previous_time = dict(previous_time) if isinstance(previous_time, dict) else {}
        _set_tool_part_state(
            part,
            {
                **previous_state,
                "status": "error",
                "error": event.error or event.text,
                "metadata": self._event_metadata(event),
                "time": {
                    "start": previous_time.get("start") or part.created_at,
                    "end": utc_now_iso(),
                },
            },
        )

    def _handle_provider_error(self, event: LLMEvent) -> None:
        self._finalize_open_text_parts()
        message = self._ensure_assistant_message(event.message_id)
        message.append_part(
            MessagePart.error_part(
                event.error or event.text or "Provider error",
                metadata=self._event_metadata(event),
            )
        )
        message.status = "error"
        message.completed_at = message.completed_at or utc_now_iso()
        self.session.status = RuntimeStatus.ERROR
        self._record_runtime_event("llm.provider_error", event)

    def _ensure_pending_tool_call_part(
        self,
        event: LLMEvent,
        draft: _ToolInputDraft,
    ) -> MessagePart | None:
        if self.current_message is None:
            return None
        part = self._find_current_tool_call_part(draft.call_id)
        if part is not None:
            _set_tool_part_state(part, _pending_tool_state(draft))
            return part

        tool_call = ToolCall(
            call_id=draft.call_id,
            tool_name=draft.name or event.tool_name or "_pending",
            arguments={},
            arguments_text="",
            status="pending",
            raw=dict(draft.raw),
        )
        return self.current_message.append_part(
            MessagePart.tool_call_part(
                tool_call,
                metadata={
                    **dict(event.metadata),
                    "tool_state": _pending_tool_state(draft),
                },
            )
        )

    def _update_pending_tool_input_state(
        self,
        event: LLMEvent,
        draft: _ToolInputDraft,
    ) -> None:
        part = self._ensure_pending_tool_call_part(event, draft)
        if part is None:
            return
        if part.tool_call is not None:
            if draft.name and part.tool_call.tool_name == "_pending":
                part.tool_call.tool_name = draft.name
            part.tool_call.arguments_text = draft.arguments_text
            part.tool_call.raw.update(dict(draft.raw))
        _set_tool_part_state(part, _pending_tool_state(draft))

    def _find_current_tool_call_part(self, call_id: str) -> MessagePart | None:
        if self.current_message is None:
            return None
        for part in self.current_message.parts:
            if (
                part.type is MessagePartType.TOOL_CALL
                and part.tool_call is not None
                and part.tool_call.call_id == call_id
            ):
                return part
        return None

    def _find_tool_call_part(self, call_id: str) -> MessagePart | None:
        for message in reversed(self.session.messages):
            if message.role is not MessageRole.ASSISTANT:
                continue
            for part in message.parts:
                if (
                    part.type is MessagePartType.TOOL_CALL
                    and part.tool_call is not None
                    and part.tool_call.call_id == call_id
                ):
                    return part
        return None

    def _tool_input_raw(self, tool_call: ToolCall) -> str:
        draft = self._tool_inputs.get(tool_call.call_id)
        if draft is not None:
            return draft.arguments_text
        return tool_call.arguments_text

    def _tool_input_ended(self, tool_call: ToolCall) -> bool:
        draft = self._tool_inputs.get(tool_call.call_id)
        if draft is not None:
            return draft.input_ended
        state = self._existing_tool_state(tool_call.call_id)
        return bool(state.get("input_ended", False))

    def _existing_tool_state(self, call_id: str) -> dict[str, Any]:
        part = self._find_tool_call_part(call_id)
        if part is None:
            return {}
        state = part.metadata.get("tool_state")
        return dict(state) if isinstance(state, dict) else {}

    def _update_tool_call_part_from_result(self, result) -> None:
        part = self._find_tool_call_part(result.call_id)
        if part is None or part.tool_call is None:
            return
        previous_state = part.metadata.get("tool_state")
        previous_state = dict(previous_state) if isinstance(previous_state, dict) else {}
        previous_time = previous_state.get("time")
        previous_time = dict(previous_time) if isinstance(previous_time, dict) else {}
        status = "completed" if result.success else "error"
        part.tool_call.status = status
        time_state = {
            "start": previous_time.get("start") or part.created_at,
            "end": utc_now_iso(),
        }
        state: dict[str, Any] = {
            "status": status,
            "input": previous_state.get("input", dict(part.tool_call.arguments)),
            "raw": previous_state.get("raw", part.tool_call.arguments_text),
            "input_ended": previous_state.get("input_ended", True),
            "metadata": dict(result.metadata),
            "time": time_state,
        }
        if result.success:
            state["output"] = result.output if result.output is not None else result.content
        else:
            state["error"] = result.error or result.content
        title = result.metadata.get("title")
        if title is not None:
            state["title"] = str(title)
        _set_tool_part_state(part, state)

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
                    "metadata": dict(event.metadata),
                    "provider_metadata": dict(event.provider_metadata),
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


def _optional_metadata_str(metadata: Mapping[str, Any], key: str) -> Optional[str]:
    value = metadata.get(key)
    if value in (None, ""):
        return None
    return str(value)


def _pending_tool_state(draft: _ToolInputDraft) -> dict[str, Any]:
    return {
        "status": "pending",
        "input": {},
        "raw": draft.arguments_text,
        "input_ended": draft.input_ended,
    }


def _set_tool_part_state(part: MessagePart, state: Mapping[str, Any]) -> None:
    part.metadata["tool_state"] = dict(state)
