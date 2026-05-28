"""Structured Runtime v2 history rendering for provider requests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Union

from ..compaction.strategy import PartAwareCompactionStrategy
from ..llm.request import (
    JsonObject,
    PreparedProviderRequest,
    ProviderRequest,
    RequestAttachment,
    RequestContext,
    RequestMessage,
    RequestMessagePart,
    RequestReasoning,
    RequestToolCall,
    RequestToolResult,
    RequestToolSchema,
)
from ..session.models import CompactionPart, Message, MessagePart, MessagePartType, Session
from ..tools.definition import ToolDef
from ..types import Attachment, ToolCall, ToolResult


HistoryInput = Union[Session, Iterable[Message]]


def render_history(
    history: HistoryInput,
    *,
    tools: Iterable[ToolDef] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProviderRequest:
    """Render Runtime v2 history and tools into a provider-neutral request."""

    messages, request_metadata = _coerce_history(history)
    request_metadata.update(_copy_mapping(metadata or {}))
    return ProviderRequest(
        messages=render_messages(messages),
        tools=render_tool_schemas(tools),
        metadata=request_metadata,
    )


def render_messages(history: HistoryInput) -> list[RequestMessage]:
    """Render Runtime v2 messages into ordered structured request messages.

    This renderer preserves the v2 part stream as-is. It does not synthesize,
    reorder, or repair tool call/result pairs.
    """

    messages, _ = _coerce_history(history)
    rendered: list[RequestMessage] = []
    for message in messages:
        rendered.extend(_render_message(message))
    return rendered


def render_tool_schemas(tools: Iterable[ToolDef] | None) -> list[RequestToolSchema]:
    """Render Runtime v2 tool definitions without depending on legacy schemas."""

    if tools is None:
        return []
    return [
        RequestToolSchema(
            id=tool.id,
            name=tool.id,
            description=tool.description,
            json_schema=_copy_mapping(tool.input_schema),
            metadata={
                "definition_metadata": _copy_mapping(tool.metadata),
                "permission": asdict(tool.permission),
                "output_policy": asdict(tool.output_policy),
            },
        )
        for tool in tools
    ]


def prepare_history_for_request(
    history: HistoryInput,
    *,
    tools: Iterable[ToolDef] | None = None,
    metadata: Mapping[str, Any] | None = None,
    max_parts: int | None = None,
    compaction_strategy: PartAwareCompactionStrategy | None = None,
) -> PreparedProviderRequest:
    """Optionally compact history before rendering a provider request."""

    if max_parts is not None and max_parts < 1:
        raise ValueError("max_parts must be at least 1")

    messages, request_metadata = _coerce_history(history)
    request_metadata.update(_copy_mapping(metadata or {}))

    limit = max_parts
    if limit is None and compaction_strategy is not None:
        limit = compaction_strategy.max_parts

    compaction_metadata: JsonObject = {}
    compaction_applied = False
    if limit is not None and _part_count(messages) > limit:
        strategy = compaction_strategy or PartAwareCompactionStrategy(max_parts=limit)
        result = strategy.compact(messages)
        messages = result.messages
        compaction_applied = result.compacted
        if result.compacted:
            compaction_metadata = {
                "max_parts": limit,
                "compacted_part_count": result.compacted_part_count,
                "compacted_message_count": result.compacted_message_count,
                "compacted_tool_pair_count": result.compacted_tool_pair_count,
            }
            request_metadata["compaction"] = dict(compaction_metadata)

    return PreparedProviderRequest(
        request=ProviderRequest(
            messages=render_messages(messages),
            tools=render_tool_schemas(tools),
            metadata=request_metadata,
        ),
        compaction_applied=compaction_applied,
        compaction_metadata=compaction_metadata,
    )


def _render_message(message: Message) -> list[RequestMessage]:
    role = message.role.value
    metadata = _message_metadata(message)
    rendered: list[RequestMessage] = []
    current_parts: list[RequestMessagePart] = []

    def flush_current() -> None:
        if not current_parts:
            return
        rendered.append(RequestMessage(role=role, parts=list(current_parts), metadata=dict(metadata)))
        current_parts.clear()

    for part in message.parts:
        if part.type is MessagePartType.COMPACTION:
            flush_current()
            rendered.append(_render_compaction_message(message, part))
            continue
        current_parts.append(_render_part(part))

    flush_current()
    return rendered


def _render_part(part: MessagePart) -> RequestMessagePart:
    metadata = _part_metadata(part)
    if part.type is MessagePartType.TEXT:
        return RequestMessagePart(type="text", text=part.text or "", metadata=metadata)
    if part.type is MessagePartType.REASONING:
        reasoning = RequestReasoning(text=part.reasoning or "", metadata=metadata)
        return RequestMessagePart(type="reasoning", reasoning=reasoning, metadata=metadata)
    if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
        return RequestMessagePart(
            type="tool_call",
            tool_call=_render_tool_call(part.tool_call, metadata),
            metadata=metadata,
        )
    if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
        return RequestMessagePart(
            type="tool_result",
            tool_result=_render_tool_result(part.tool_result, metadata),
            metadata=metadata,
        )
    if part.type is MessagePartType.ATTACHMENT and part.attachment is not None:
        attachment = _render_attachment(part.attachment)
        return RequestMessagePart(
            type="attachment",
            attachment=attachment,
            context=RequestContext(
                type="attachment",
                metadata={
                    **metadata,
                    **_attachment_metadata(part.attachment),
                },
            ),
            metadata=metadata,
        )
    if part.type is MessagePartType.TASK and part.task is not None:
        task_metadata = {
            "task_id": part.task.task_id,
            "description": part.task.description,
            "status": part.task.status,
            "agent": part.task.agent,
            "model": part.task.model,
            "metadata": _copy_mapping(part.task.metadata),
        }
        return RequestMessagePart(
            type="context",
            context=RequestContext(
                type="task",
                text=part.task.prompt,
                metadata={**metadata, "task": task_metadata},
            ),
            metadata=metadata,
        )
    if part.type is MessagePartType.ERROR:
        return RequestMessagePart(type="error", text=part.text or "", metadata=metadata)
    raise ValueError(f"unsupported message part type: {part.type.value}")


def _render_compaction_message(message: Message, part: MessagePart) -> RequestMessage:
    if part.compaction is None:
        raise ValueError("compaction part requires compaction payload")

    metadata = _message_metadata(message)
    part_metadata = _part_metadata(part)
    context = RequestContext(
        type="compaction_summary",
        text=part.compaction.summary,
        metadata={
            **part_metadata,
            "compaction": _compaction_metadata(part.compaction),
            "source_role": message.role.value,
        },
    )
    return RequestMessage(
        role="system",
        parts=[RequestMessagePart(type="context", context=context, metadata=part_metadata)],
        metadata={**metadata, "rendered_as": "system_context"},
    )


def _render_tool_call(tool_call: ToolCall, source_metadata: Mapping[str, Any]) -> RequestToolCall:
    return RequestToolCall(
        call_id=tool_call.call_id,
        tool_name=tool_call.tool_name,
        arguments=_copy_mapping(tool_call.arguments),
        arguments_text=tool_call.arguments_text,
        status=tool_call.status,
        call_type=tool_call.call_type,
        raw=_copy_mapping(tool_call.raw),
        metadata={
            **_copy_mapping(source_metadata),
            "tool_call_metadata": _copy_mapping(tool_call.metadata),
        },
        created_at=tool_call.created_at,
    )


def _render_tool_result(
    tool_result: ToolResult,
    source_metadata: Mapping[str, Any],
) -> RequestToolResult:
    return RequestToolResult(
        call_id=tool_result.call_id,
        tool_name=tool_result.tool_name,
        content=tool_result.content,
        output=_copy_value(tool_result.output),
        success=tool_result.success,
        error=tool_result.error,
        status=tool_result.status,
        truncated=tool_result.truncated,
        attachments=[_render_attachment(attachment) for attachment in tool_result.attachments],
        events=_copy_value(tool_result.events),
        metadata={
            **_copy_mapping(source_metadata),
            "tool_result_metadata": _copy_mapping(tool_result.metadata),
        },
        created_at=tool_result.created_at,
    )


def _render_attachment(attachment: Attachment) -> RequestAttachment:
    return RequestAttachment(
        attachment_id=attachment.attachment_id,
        mime_type=attachment.mime_type,
        filename=attachment.filename,
        url=attachment.url,
        text_ref=attachment.text_ref,
        metadata=_copy_mapping(attachment.metadata),
        created_at=attachment.created_at,
    )


def _coerce_history(history: HistoryInput) -> tuple[list[Message], JsonObject]:
    if isinstance(history, Session):
        return list(history.messages), _session_metadata(history)
    return list(history), {}


def _session_metadata(session: Session) -> JsonObject:
    return {
        "session_id": session.session_id,
        "session_title": session.title,
        "session_metadata": _copy_mapping(session.metadata),
        "session_created_at": session.created_at,
        "session_updated_at": session.updated_at,
    }


def _message_metadata(message: Message) -> JsonObject:
    return {
        "source_message_id": message.message_id,
        "source_session_id": message.session_id,
        "source_role": message.role.value,
        "parent_message_id": message.parent_message_id,
        "message_status": message.status,
        "message_usage": _copy_mapping(message.usage),
        "message_metadata": _copy_mapping(message.metadata),
        "created_at": message.created_at,
        "completed_at": message.completed_at,
    }


def _part_metadata(part: MessagePart) -> JsonObject:
    return {
        "source_part_id": part.part_id,
        "source_message_id": part.message_id,
        "source_session_id": part.session_id,
        "part_type": part.type.value,
        "part_metadata": _copy_mapping(part.metadata),
        "created_at": part.created_at,
    }


def _compaction_metadata(compaction: CompactionPart) -> JsonObject:
    return {
        "summary": compaction.summary,
        "source_message_ids": list(compaction.source_message_ids),
        "auto": compaction.auto,
        "overflow": compaction.overflow,
        "tail_start_message_id": compaction.tail_start_message_id,
        "original_part_count": compaction.original_part_count,
        "original_message_count": compaction.original_message_count,
        "tool_pair_count": compaction.tool_pair_count,
        "metadata": _copy_mapping(compaction.metadata),
    }


def _attachment_metadata(attachment: Attachment) -> JsonObject:
    return {
        "attachment_id": attachment.attachment_id,
        "mime_type": attachment.mime_type,
        "filename": attachment.filename,
        "url": attachment.url,
        "text_ref": attachment.text_ref,
        "metadata": _copy_mapping(attachment.metadata),
        "created_at": attachment.created_at,
    }


def _part_count(messages: Iterable[Message]) -> int:
    return sum(len(message.parts) for message in messages)


def _copy_mapping(mapping: Mapping[str, Any]) -> JsonObject:
    copied = _copy_value(dict(mapping))
    if isinstance(copied, dict):
        return copied
    return dict(mapping)


def _copy_value(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value

