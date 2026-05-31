"""Runtime event bridge for normalized LLM streams."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping
from typing import Any, List, Optional, Union

from ..events import RuntimeEvent
from ..llm.events import LLMEvent, LLMEventType


_RUNTIME_EVENT_TYPES = {
    LLMEventType.STEP_START.value: "llm.step_start",
    LLMEventType.TEXT_DELTA.value: "llm.text_delta",
    LLMEventType.REASONING_DELTA.value: "llm.reasoning_delta",
    LLMEventType.TOOL_INPUT_START.value: "llm.tool_call_delta",
    LLMEventType.TOOL_INPUT_DELTA.value: "llm.tool_call_delta",
    LLMEventType.TOOL_INPUT_END.value: "llm.tool_call_delta",
    LLMEventType.TOOL_CALL_COMPLETE.value: "llm.tool_call_done",
    LLMEventType.STEP_FINISH.value: "llm.step_finish",
    LLMEventType.ERROR.value: "llm.error",
}


async def bridge_llm_stream_events(
    events: Union[AsyncIterable[LLMEvent], Iterable[LLMEvent]],
    *,
    runtime_events: List[RuntimeEvent],
    session_id: str,
    run_id: str,
    iteration: int,
    enabled: bool = True,
) -> AsyncIterator[LLMEvent]:
    """Yield LLM events while appending observable runtime stream events."""

    if hasattr(events, "__aiter__"):
        async for event in events:  # type: ignore[union-attr]
            if enabled:
                _append_runtime_event(
                    event,
                    runtime_events=runtime_events,
                    session_id=session_id,
                    run_id=run_id,
                    iteration=iteration,
                )
            yield event
        return

    for event in events:  # type: ignore[union-attr]
        if enabled:
            _append_runtime_event(
                event,
                runtime_events=runtime_events,
                session_id=session_id,
                run_id=run_id,
                iteration=iteration,
            )
        yield event


def runtime_event_from_llm_event(
    event: LLMEvent,
    *,
    session_id: str,
    run_id: str,
    iteration: int,
) -> Optional[RuntimeEvent]:
    runtime_type = _RUNTIME_EVENT_TYPES.get(event.type_value)
    if runtime_type is None:
        return None

    payload: dict[str, Any] = {
        "run_id": run_id,
        "iteration": iteration,
        "llm_event_type": event.type_value,
    }
    _set_if_present(payload, "event_id", _event_id(event))

    if event.type_value in (
        LLMEventType.TEXT_DELTA.value,
        LLMEventType.REASONING_DELTA.value,
    ):
        delta = event.delta or event.text
        _set_if_present(payload, "delta", delta)
        _set_if_present(payload, "text", event.text or delta)

    if event.type_value in (
        LLMEventType.TOOL_INPUT_START.value,
        LLMEventType.TOOL_INPUT_DELTA.value,
        LLMEventType.TOOL_INPUT_END.value,
        LLMEventType.TOOL_CALL_COMPLETE.value,
    ):
        _set_if_present(payload, "tool_call_id", event.tool_call_id)
        _set_if_present(payload, "tool_name", event.tool_name)
        _add_tool_arguments(payload, event)

    if event.type_value == LLMEventType.STEP_FINISH.value and event.usage:
        payload["usage"] = dict(event.usage)
    if event.metadata.get("finish_reason"):
        payload["finish_reason"] = event.metadata["finish_reason"]
    if event.type_value == LLMEventType.ERROR.value:
        _set_if_present(payload, "error", event.error or event.text)

    return RuntimeEvent(
        type=runtime_type,
        message=event.error or "",
        session_id=session_id,
        message_id=event.message_id,
        part_id=event.part_id,
        payload=payload,
    )


def _append_runtime_event(
    event: LLMEvent,
    *,
    runtime_events: List[RuntimeEvent],
    session_id: str,
    run_id: str,
    iteration: int,
) -> None:
    runtime_event = runtime_event_from_llm_event(
        event,
        session_id=session_id,
        run_id=run_id,
        iteration=iteration,
    )
    if runtime_event is not None:
        runtime_events.append(runtime_event)


def _add_tool_arguments(payload: dict[str, Any], event: LLMEvent) -> None:
    if event.type_value == LLMEventType.TOOL_INPUT_DELTA.value:
        _set_if_present(payload, "arguments_delta", event.delta or event.text)
        return

    if event.type_value == LLMEventType.TOOL_INPUT_END.value:
        _set_if_present(payload, "arguments", event.text or event.delta)
        return

    if event.type_value != LLMEventType.TOOL_CALL_COMPLETE.value:
        return

    tool_call = event.tool_call
    if tool_call is not None:
        payload["arguments"] = dict(tool_call.arguments)
        _set_if_present(payload, "arguments_text", tool_call.arguments_text)
        if not payload.get("tool_call_id"):
            payload["tool_call_id"] = tool_call.call_id
        if not payload.get("tool_name"):
            payload["tool_name"] = tool_call.tool_name
        return

    _set_if_present(payload, "arguments", event.text or event.delta)


def _event_id(event: LLMEvent) -> Optional[str]:
    for source in (event.metadata, event.raw):
        value = _mapping_value(source, "event_id")
        if value is not None:
            return str(value)
        value = _mapping_value(source, "id")
        if value is not None:
            return str(value)
    for key in ("item_id", "response_id"):
        value = _mapping_value(event.raw, key)
        if value is not None:
            return str(value)
    return None


def _mapping_value(mapping: Mapping[str, Any], key: str) -> Optional[Any]:
    value = mapping.get(key)
    if value in (None, ""):
        return None
    return value


def _set_if_present(payload: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, ""):
        return
    payload[key] = value


__all__ = ["bridge_llm_stream_events", "runtime_event_from_llm_event"]
