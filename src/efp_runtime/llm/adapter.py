"""Provider output normalization for EFP runtime."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Mapping, Optional, Protocol, Union

from .events import LLMEvent, LLMEventType
from ..types import ToolCall, ToolResult


@dataclass
class _ToolCallDraft:
    index: int
    id: str
    name: str = ""
    arguments_chunks: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)
    started: bool = False
    input_ended: bool = False
    completed: bool = False

    @property
    def arguments_text(self) -> str:
        return "".join(self.arguments_chunks)


class LLMEventAdapter(Protocol):
    def normalize_response(self, response: Mapping[str, Any]) -> Iterable[LLMEvent]:
        ...

    async def normalize_stream(
        self,
        chunks: Union[AsyncIterable[Mapping[str, Any]], Iterable[Mapping[str, Any]]],
    ) -> AsyncIterator[LLMEvent]:
        ...


class DefaultLLMEventAdapter:
    """Normalize common provider outputs into EFP runtime LLM events."""

    def normalize_response(self, response: Mapping[str, Any]) -> Iterable[LLMEvent]:
        raw = dict(response)
        usage = _dict_or_empty(raw.get("usage"))
        yield LLMEvent(LLMEventType.STEP_START, raw=raw)

        if "error" in raw and raw.get("error"):
            yield LLMEvent(
                LLMEventType.ERROR,
                error=_format_error(raw.get("error")),
                raw=raw,
            )
            yield LLMEvent(LLMEventType.STEP_FINISH, usage=usage, raw=raw)
            return

        yield LLMEvent(LLMEventType.MESSAGE_START, raw=raw)

        reasoning = _extract_response_reasoning(raw)
        if isinstance(reasoning, str) and reasoning:
            yield LLMEvent(
                LLMEventType.REASONING_DELTA,
                delta=reasoning,
                text=reasoning,
                raw=raw,
            )

        content = _extract_response_text(raw)
        if content:
            part_id = _stable_part_id("text", 0)
            yield LLMEvent(LLMEventType.TEXT_START, part_id=part_id, raw=raw)
            yield LLMEvent(
                LLMEventType.TEXT_DELTA,
                part_id=part_id,
                delta=content,
                text=content,
                raw=raw,
            )
            yield LLMEvent(LLMEventType.TEXT_END, part_id=part_id, text=content, raw=raw)

        for index, tool_call in enumerate(_extract_tool_calls(raw)):
            yield LLMEvent(
                LLMEventType.TOOL_INPUT_START,
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                raw=tool_call.raw,
            )
            if tool_call.arguments_text:
                yield LLMEvent(
                    LLMEventType.TOOL_INPUT_DELTA,
                    tool_call_id=tool_call.call_id,
                    tool_name=tool_call.tool_name,
                    delta=tool_call.arguments_text,
                    text=tool_call.arguments_text,
                    raw=tool_call.raw,
                )
            yield LLMEvent(
                LLMEventType.TOOL_INPUT_END,
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                text=tool_call.arguments_text,
                raw=tool_call.raw,
            )
            yield LLMEvent(
                LLMEventType.TOOL_CALL_COMPLETE,
                tool_call_id=tool_call.call_id,
                tool_name=tool_call.tool_name,
                tool_call=tool_call,
                metadata={"index": index},
                raw=tool_call.raw,
            )

        for result in _extract_tool_results(raw):
            yield LLMEvent(
                LLMEventType.TOOL_RESULT,
                tool_call_id=result.call_id,
                tool_name=result.tool_name,
                tool_result=result,
                raw=raw,
            )

        yield LLMEvent(LLMEventType.STEP_FINISH, usage=usage, raw=raw)

    async def normalize_stream(
        self,
        chunks: Union[AsyncIterable[Mapping[str, Any]], Iterable[Mapping[str, Any]]],
    ) -> AsyncIterator[LLMEvent]:
        started = False
        text_started = False
        text_part_id = _stable_part_id("text", 0)
        tool_drafts: Dict[int, _ToolCallDraft] = {}
        response_tool_drafts: Dict[str, _ToolCallDraft] = {}
        usage: Dict[str, Any] = {}
        finish_reason: Optional[str] = None

        async for chunk in _aiter_chunks(chunks):
            if isinstance(chunk, LLMEvent):
                if not started and chunk.type != LLMEventType.STEP_START:
                    yield LLMEvent(LLMEventType.STEP_START)
                    started = True
                yield chunk
                continue

            raw = dict(chunk)
            if not started:
                yield LLMEvent(LLMEventType.STEP_START, raw=raw)
                yield LLMEvent(LLMEventType.MESSAGE_START, raw=raw)
                started = True

            if raw.get("usage"):
                usage = _dict_or_empty(raw.get("usage"))

            response_events, consumed_response_chunk = self._normalize_responses_stream_chunk(
                raw,
                response_tool_drafts,
            )
            for event in response_events:
                if event.type == LLMEventType.TEXT_DELTA and not text_started:
                    yield LLMEvent(LLMEventType.TEXT_START, part_id=text_part_id, raw=raw)
                    text_started = True
                if event.type in (LLMEventType.TEXT_DELTA, LLMEventType.TEXT_END):
                    event.part_id = event.part_id or text_part_id
                yield event
            if consumed_response_chunk:
                finish_reason = finish_reason or _extract_finish_reason(raw)
                continue

            for event in self._normalize_simple_chunk(raw):
                if event.type == LLMEventType.TEXT_DELTA and not text_started:
                    yield LLMEvent(LLMEventType.TEXT_START, part_id=text_part_id, raw=raw)
                    text_started = True
                if event.type in (LLMEventType.TEXT_DELTA, LLMEventType.TEXT_END):
                    event.part_id = event.part_id or text_part_id
                yield event

            chat_delta = _extract_chat_stream_delta(raw)
            if chat_delta is None:
                finish_reason = finish_reason or _extract_finish_reason(raw)
                continue

            finish_reason = _extract_finish_reason(raw) or finish_reason
            content_delta = chat_delta.get("content")
            if isinstance(content_delta, str) and content_delta:
                if not text_started:
                    yield LLMEvent(LLMEventType.TEXT_START, part_id=text_part_id, raw=raw)
                    text_started = True
                yield LLMEvent(
                    LLMEventType.TEXT_DELTA,
                    part_id=text_part_id,
                    delta=content_delta,
                    text=content_delta,
                    raw=raw,
                )

            reasoning_delta = chat_delta.get("reasoning") or chat_delta.get("reasoning_content")
            if isinstance(reasoning_delta, str) and reasoning_delta:
                yield LLMEvent(
                    LLMEventType.REASONING_DELTA,
                    delta=reasoning_delta,
                    text=reasoning_delta,
                    raw=raw,
                )

            for tool_delta in _as_list(chat_delta.get("tool_calls")):
                if not isinstance(tool_delta, Mapping):
                    continue
                index = _coerce_int(tool_delta.get("index"), default=len(tool_drafts))
                draft = tool_drafts.get(index)
                if draft is None:
                    draft = _ToolCallDraft(
                        index=index,
                        id=str(tool_delta.get("id") or f"call_{index}"),
                        raw=dict(tool_delta),
                    )
                    tool_drafts[index] = draft
                elif tool_delta.get("id"):
                    draft.id = str(tool_delta.get("id"))
                draft.raw.update(dict(tool_delta))

                function_delta = tool_delta.get("function")
                if isinstance(function_delta, Mapping):
                    if function_delta.get("name"):
                        draft.name += str(function_delta.get("name"))
                    if function_delta.get("arguments"):
                        draft.arguments_chunks.append(str(function_delta.get("arguments")))

                if not draft.started:
                    yield LLMEvent(
                        LLMEventType.TOOL_INPUT_START,
                        tool_call_id=draft.id,
                        tool_name=draft.name,
                        raw=draft.raw,
                    )
                    draft.started = True
                if function_delta and function_delta.get("arguments"):
                    yield LLMEvent(
                        LLMEventType.TOOL_INPUT_DELTA,
                        tool_call_id=draft.id,
                        tool_name=draft.name,
                        delta=str(function_delta.get("arguments")),
                        text=str(function_delta.get("arguments")),
                        raw=draft.raw,
                    )

        if started and text_started:
            yield LLMEvent(LLMEventType.TEXT_END, part_id=text_part_id)

        for draft in sorted(tool_drafts.values(), key=lambda item: item.index):
            if draft.started:
                yield LLMEvent(
                    LLMEventType.TOOL_INPUT_END,
                    tool_call_id=draft.id,
                    tool_name=draft.name,
                    text=draft.arguments_text,
                    raw=draft.raw,
                )
                tool_call = _make_tool_call(
                    {
                        "id": draft.id,
                        "type": "function",
                        "function": {
                            "name": draft.name,
                            "arguments": draft.arguments_text,
                        },
                    },
                    draft.index,
                )
                yield LLMEvent(
                    LLMEventType.TOOL_CALL_COMPLETE,
                    tool_call_id=tool_call.call_id,
                    tool_name=tool_call.tool_name,
                    tool_call=tool_call,
                    raw=draft.raw,
                )

        for draft in sorted(response_tool_drafts.values(), key=lambda item: item.index):
            for event in _complete_response_tool_draft(draft):
                yield event

        if started:
            metadata = {"finish_reason": finish_reason} if finish_reason else {}
            yield LLMEvent(LLMEventType.STEP_FINISH, usage=usage, metadata=metadata)

    def _normalize_responses_stream_chunk(
        self,
        raw: Mapping[str, Any],
        response_tool_drafts: Dict[str, _ToolCallDraft],
    ) -> tuple[List[LLMEvent], bool]:
        response_type = raw.get("type")
        if not isinstance(response_type, str) or not response_type.startswith("response."):
            return [], False

        if response_type in {"response.output_text.delta"}:
            delta = str(raw.get("delta") or "")
            return [LLMEvent(LLMEventType.TEXT_DELTA, delta=delta, text=delta, raw=dict(raw))], True

        if response_type in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
        }:
            delta = str(raw.get("delta") or "")
            return [LLMEvent(LLMEventType.REASONING_DELTA, delta=delta, text=delta, raw=dict(raw))], True

        if response_type == "response.function_call_arguments.delta":
            call_id = _response_call_id(raw, response_tool_drafts)
            draft = _response_tool_draft(response_tool_drafts, call_id, raw)
            delta = str(raw.get("delta") or "")
            events = _ensure_response_tool_started(draft)
            if delta:
                draft.arguments_chunks.append(delta)
                draft.raw.update(dict(raw))
                events.append(
                    LLMEvent(
                        LLMEventType.TOOL_INPUT_DELTA,
                        tool_call_id=draft.id,
                        tool_name=draft.name,
                        delta=delta,
                        text=delta,
                        raw=dict(draft.raw),
                    )
                )
            return events, True

        if response_type in {"response.output_item.added", "response.output_item.done"}:
            item = raw.get("item")
            if not isinstance(item, Mapping) or not _is_responses_function_call_item(item):
                return [], True
            call_id = _response_item_draft_key(raw, item, response_tool_drafts)
            draft = _response_tool_draft(response_tool_drafts, call_id, raw, item=item)
            events = _ensure_response_tool_started(draft)
            arguments = item.get("arguments")
            if arguments not in (None, "") and not draft.arguments_text:
                arguments_text = _copilot_stream_value_text(arguments)
                draft.arguments_chunks.append(arguments_text)
                events.append(
                    LLMEvent(
                        LLMEventType.TOOL_INPUT_DELTA,
                        tool_call_id=draft.id,
                        tool_name=draft.name,
                        delta=arguments_text,
                        text=arguments_text,
                        raw=dict(draft.raw),
                    )
                )
            if response_type == "response.output_item.done":
                events.extend(_complete_response_tool_draft(draft))
            return events, True

        if response_type == "response.function_call_arguments.done":
            call_id = _response_call_id(raw, response_tool_drafts)
            draft = _response_tool_draft(response_tool_drafts, call_id, raw)
            arguments = raw.get("arguments")
            if arguments not in (None, "") and not draft.arguments_text:
                draft.arguments_chunks.append(_copilot_stream_value_text(arguments))
            events = _ensure_response_tool_started(draft)
            events.extend(_end_response_tool_input(draft))
            return events, True

        return [], True

    def _normalize_simple_chunk(self, raw: Mapping[str, Any]) -> List[LLMEvent]:
        event_type = raw.get("type") or raw.get("event")
        if isinstance(event_type, str) and event_type in _LLM_EVENT_TYPE_VALUES:
            return [
                LLMEvent(
                    LLMEventType(event_type),
                    message_id=_optional_str(raw.get("message_id")),
                    part_id=_optional_str(raw.get("part_id")),
                    tool_call_id=_optional_str(raw.get("tool_call_id") or raw.get("call_id")),
                    tool_name=_optional_str(raw.get("tool_name") or raw.get("name")),
                    delta=str(raw.get("delta") or ""),
                    text=str(raw.get("text") or ""),
                    raw=dict(raw),
                )
            ]

        response_type = raw.get("type")
        if response_type in {"response.output_text.delta", "output_text.delta"}:
            delta = str(raw.get("delta") or "")
            return [LLMEvent(LLMEventType.TEXT_DELTA, delta=delta, text=delta, raw=dict(raw))]
        if response_type in {
            "response.reasoning_summary_text.delta",
            "response.reasoning_text.delta",
            "reasoning_delta",
        }:
            delta = str(raw.get("delta") or "")
            return [LLMEvent(LLMEventType.REASONING_DELTA, delta=delta, text=delta, raw=dict(raw))]
        if response_type in {
            "response.function_call_arguments.delta",
            "function_call_arguments.delta",
        }:
            call_id = _optional_str(raw.get("call_id") or raw.get("item_id"))
            delta = str(raw.get("delta") or "")
            return [
                LLMEvent(
                    LLMEventType.TOOL_INPUT_DELTA,
                    tool_call_id=call_id,
                    delta=delta,
                    text=delta,
                    raw=dict(raw),
                )
            ]

        if isinstance(raw.get("content"), str):
            delta = str(raw.get("content") or "")
            return [LLMEvent(LLMEventType.TEXT_DELTA, delta=delta, text=delta, raw=dict(raw))]
        if isinstance(raw.get("delta"), str):
            delta = str(raw.get("delta") or "")
            return [LLMEvent(LLMEventType.TEXT_DELTA, delta=delta, text=delta, raw=dict(raw))]
        return []


async def _aiter_chunks(
    chunks: Union[AsyncIterable[Mapping[str, Any]], Iterable[Mapping[str, Any]]],
) -> AsyncIterator[Mapping[str, Any]]:
    if hasattr(chunks, "__aiter__"):
        async for chunk in chunks:  # type: ignore[union-attr]
            yield chunk
        return
    for chunk in chunks:  # type: ignore[union-attr]
        yield chunk


def _extract_response_text(response: Mapping[str, Any]) -> str:
    content = _extract_text(response.get("content"))
    if content:
        return content

    chat_message = _extract_chat_response_message(response)
    if chat_message is not None:
        content = _extract_text(chat_message.get("content"))
        if content:
            return content

    output_text = _extract_text(response.get("output_text"))
    if output_text:
        return output_text

    chunks: List[str] = []
    for item in _as_list(response.get("output")):
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type in {"message", "assistant_message"}:
            chunks.append(_extract_text(item.get("content")))
        elif item_type in {"text", "output_text"}:
            chunks.append(str(item.get("text") or ""))
    return "".join(chunks)


def _extract_response_reasoning(response: Mapping[str, Any]) -> Optional[str]:
    reasoning = response.get("reasoning")
    if isinstance(reasoning, str):
        return reasoning

    chat_message = _extract_chat_response_message(response)
    if chat_message is not None:
        reasoning = chat_message.get("reasoning") or chat_message.get("reasoning_content")
        if isinstance(reasoning, str):
            return reasoning

    chunks: List[str] = []
    for item in _as_list(response.get("output")):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "reasoning":
            chunks.append(
                _extract_text(item.get("content")) or str(item.get("text") or "")
            )
    return "".join(chunks) or None


def _extract_tool_calls(response: Mapping[str, Any]) -> List[ToolCall]:
    raw_function_calls = _as_list(response.get("function_calls"))
    raw_tool_calls = _as_list(response.get("tool_calls"))
    chat_message = _extract_chat_response_message(response)
    if chat_message is not None:
        raw_function_calls.extend(_as_list(chat_message.get("function_calls")))
        raw_tool_calls.extend(_as_list(chat_message.get("tool_calls")))
    raw_function_calls.extend(_extract_responses_function_calls(response))
    normalized: List[ToolCall] = []
    seen = set()

    for raw_call in raw_function_calls + raw_tool_calls:
        if not isinstance(raw_call, Mapping):
            continue
        tool_call = _make_tool_call(raw_call, len(normalized))
        dedupe_key = tool_call.call_id or f"{tool_call.tool_name}:{tool_call.arguments_text}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(tool_call)
    return normalized


def _extract_chat_response_message(response: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return None
    message = choice.get("message")
    if isinstance(message, Mapping):
        return message
    return None


def _extract_responses_function_calls(response: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    calls: List[Mapping[str, Any]] = []
    for item in _as_list(response.get("output")):
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "function_call":
            calls.append(item)
            continue
        for content_item in _as_list(item.get("content")):
            if (
                isinstance(content_item, Mapping)
                and content_item.get("type") == "function_call"
            ):
                calls.append(content_item)
    return calls


def _extract_tool_results(response: Mapping[str, Any]) -> List[ToolResult]:
    results = []
    for raw_result in _as_list(response.get("tool_results")):
        if not isinstance(raw_result, Mapping):
            continue
        call_id = str(raw_result.get("call_id") or raw_result.get("tool_call_id") or "")
        if not call_id:
            continue
        is_error = bool(raw_result.get("is_error") or raw_result.get("error"))
        error = _optional_str(raw_result.get("error")) if is_error else None
        results.append(
            ToolResult(
                call_id=call_id,
                tool_name=_optional_str(
                    raw_result.get("tool_name")
                    or raw_result.get("tool_id")
                    or raw_result.get("name")
                )
                or "tool",
                output=raw_result.get("output", raw_result.get("content")),
                success=not is_error,
                error=error,
                metadata=_dict_or_empty(raw_result.get("metadata")),
            )
        )
    return results


def _make_tool_call(raw_call: Mapping[str, Any], index: int) -> ToolCall:
    function = raw_call.get("function")
    function_data = function if isinstance(function, Mapping) else {}
    call_id = str(
        raw_call.get("id")
        or raw_call.get("call_id")
        or raw_call.get("tool_call_id")
        or f"call_{index}"
    )
    name = str(function_data.get("name") or raw_call.get("name") or raw_call.get("tool_name") or "")
    raw_arguments = (
        function_data.get("arguments")
        if "arguments" in function_data
        else raw_call.get("arguments", raw_call.get("input", {}))
    )
    arguments, arguments_text = _parse_arguments(raw_arguments)
    normalized_arguments = arguments if isinstance(arguments, dict) else {"value": arguments}
    return ToolCall(
        call_id=call_id,
        tool_name=name,
        arguments=normalized_arguments,
        arguments_text=arguments_text,
        type=str(raw_call.get("type") or "function"),
        raw=dict(raw_call),
    )


def _parse_arguments(value: Any) -> tuple[Any, str]:
    if value is None:
        return {}, ""
    if isinstance(value, str):
        if not value:
            return {}, ""
        try:
            return json.loads(value), value
        except json.JSONDecodeError:
            return value, value
    if isinstance(value, (dict, list)):
        return value, json.dumps(value, sort_keys=True)
    return value, str(value)


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts = []
        for item in value:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, Mapping):
                item_type = item.get("type")
                if item_type in {"text", "output_text", "input_text"}:
                    text_parts.append(str(item.get("text") or ""))
        return "".join(text_parts)
    return str(value)


def _extract_chat_stream_delta(raw: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, Mapping):
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                return delta
            message = choice.get("message")
            if isinstance(message, Mapping):
                return message
    delta = raw.get("delta")
    if isinstance(delta, Mapping):
        return delta
    return None


def _extract_finish_reason(raw: Mapping[str, Any]) -> Optional[str]:
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, Mapping) and choice.get("finish_reason"):
            return str(choice.get("finish_reason"))
    if raw.get("finish_reason"):
        return str(raw.get("finish_reason"))
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _stable_part_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index}"


def _format_error(error: Any) -> str:
    if isinstance(error, Mapping):
        message = error.get("message")
        if message:
            return str(message)
    return str(error)


def _response_call_id(
    raw: Mapping[str, Any],
    drafts: Mapping[str, _ToolCallDraft],
    *,
    fallback: Any = None,
) -> str:
    for key in ("call_id", "id", "item_id", "tool_call_id"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    if fallback not in (None, ""):
        return str(fallback)
    output_index = raw.get("output_index")
    if output_index not in (None, ""):
        return f"call_{output_index}"
    return f"call_{len(drafts)}"


def _response_tool_draft(
    drafts: Dict[str, _ToolCallDraft],
    call_id: str,
    raw: Mapping[str, Any],
    *,
    item: Mapping[str, Any] | None = None,
) -> _ToolCallDraft:
    draft = drafts.get(call_id)
    source = item if item is not None else raw
    if draft is None:
        draft = _ToolCallDraft(
            index=len(drafts),
            id=str(source.get("call_id") or source.get("id") or call_id),
            name=str(source.get("name") or source.get("tool_name") or ""),
            raw=dict(raw),
        )
        drafts[call_id] = draft
    else:
        draft.raw.update(dict(raw))
        if source.get("call_id") or source.get("id"):
            draft.id = str(source.get("call_id") or source.get("id"))
        if source.get("name") or source.get("tool_name"):
            draft.name = str(source.get("name") or source.get("tool_name"))
    if item is not None:
        draft.raw["item"] = dict(item)
    return draft


def _response_item_draft_key(
    raw: Mapping[str, Any],
    item: Mapping[str, Any],
    drafts: Mapping[str, _ToolCallDraft],
) -> str:
    for value in (raw.get("item_id"), item.get("id"), item.get("call_id")):
        if value not in (None, ""):
            return str(value)
    return _response_call_id(item, drafts)


def _ensure_response_tool_started(draft: _ToolCallDraft) -> List[LLMEvent]:
    if draft.started:
        return []
    draft.started = True
    return [
        LLMEvent(
            LLMEventType.TOOL_INPUT_START,
            tool_call_id=draft.id,
            tool_name=draft.name,
            raw=dict(draft.raw),
        )
    ]


def _end_response_tool_input(draft: _ToolCallDraft) -> List[LLMEvent]:
    if draft.input_ended:
        return []
    draft.input_ended = True
    return [
        LLMEvent(
            LLMEventType.TOOL_INPUT_END,
            tool_call_id=draft.id,
            tool_name=draft.name,
            text=draft.arguments_text,
            raw=dict(draft.raw),
        )
    ]


def _complete_response_tool_draft(draft: _ToolCallDraft) -> List[LLMEvent]:
    if draft.completed:
        return []
    events = _ensure_response_tool_started(draft)
    events.extend(_end_response_tool_input(draft))
    draft.completed = True
    tool_call = _make_tool_call(
        {
            "id": draft.id,
            "type": "function",
            "function": {
                "name": draft.name,
                "arguments": draft.arguments_text,
            },
        },
        draft.index,
    )
    events.append(
        LLMEvent(
            LLMEventType.TOOL_CALL_COMPLETE,
            tool_call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            tool_call=tool_call,
            raw=dict(draft.raw),
        )
    )
    return events


def _is_responses_function_call_item(item: Mapping[str, Any]) -> bool:
    return str(item.get("type") or "") in {"function_call", "tool_call"}


def _copilot_stream_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, (Mapping, list)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)


_LLM_EVENT_TYPE_VALUES = {event_type.value for event_type in LLMEventType}
