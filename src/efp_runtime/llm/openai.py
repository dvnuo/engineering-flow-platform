"""OpenAI-compatible request projection for EFP runtime.

This module does not call providers. It turns the provider-neutral
``ProviderRequest`` contract into plain dictionaries that a later transport can
send or adapt.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, List, Optional

from .request import (
    ProviderRequest,
    RequestAttachment,
    RequestContext,
    RequestMessage,
    RequestMessagePart,
    RequestToolCall,
    RequestToolResult,
    RequestToolSchema,
)


JsonDict = Dict[str, Any]
RESPONSES_CALL_ID_MAX_LENGTH = 64
_RESPONSES_CALL_ID_PREFIX = "call_"


def normalize_responses_call_id(call_id: Any) -> str:
    """Return a Responses-compatible call_id without changing short IDs."""

    if isinstance(call_id, str):
        original = call_id
    elif call_id is None:
        original = ""
    else:
        original = str(call_id)
    if not original or len(original) <= RESPONSES_CALL_ID_MAX_LENGTH:
        return original
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    prefix_length = RESPONSES_CALL_ID_MAX_LENGTH - len(_RESPONSES_CALL_ID_PREFIX)
    return "{0}{1}".format(_RESPONSES_CALL_ID_PREFIX, digest[:prefix_length])


def provider_request_to_openai_chat(
    request: ProviderRequest,
    *,
    model: str,
    instructions: Optional[str] = None,
    stream: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
) -> JsonDict:
    """Project a EFP runtime request into an OpenAI Chat Completions payload."""

    messages: List[JsonDict] = []
    if instructions is not None:
        messages.append({"role": "system", "content": instructions})

    for message in request.messages:
        messages.extend(request_message_to_openai_chat_messages(message))

    return {
        "model": model,
        "messages": messages,
        "tools": [request_tool_schema_to_openai_tool(tool) for tool in request.tools],
        "stream": stream,
        "metadata": _payload_metadata(request, metadata, "chat_completions"),
    }


def provider_request_to_openai_responses(
    request: ProviderRequest,
    *,
    model: str,
    instructions: Optional[str] = None,
    stream: bool = False,
    metadata: Optional[Mapping[str, Any]] = None,
    reasoning_effort: Optional[str] = None,
) -> JsonDict:
    """Project a EFP runtime request into an OpenAI Responses payload."""

    payload: JsonDict = {
        "model": model,
        "input": [request_message_to_openai_responses_input(message) for message in request.messages],
        "tools": [
            request_tool_schema_to_openai_responses_tool(tool) for tool in request.tools
        ],
        "stream": stream,
        "metadata": _payload_metadata(request, metadata, "responses"),
    }
    if instructions is not None:
        payload["instructions"] = instructions
    if reasoning_effort is not None:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def request_tool_schema_to_openai_tool(schema: RequestToolSchema) -> JsonDict:
    """Project a provider-neutral tool schema to Chat Completions tool shape."""

    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": _copy_mapping(schema.json_schema),
        },
    }


def request_tool_schema_to_openai_responses_tool(schema: RequestToolSchema) -> JsonDict:
    """Project a provider-neutral tool schema to Responses function tool shape."""

    return {
        "type": "function",
        "name": schema.name,
        "description": schema.description,
        "parameters": _copy_mapping(schema.json_schema),
    }


def request_message_to_openai_chat_messages(message: RequestMessage) -> List[JsonDict]:
    """Project one EFP runtime message to one or more Chat Completions messages."""

    projected: List[JsonDict] = []
    buffered_parts: List[RequestMessagePart] = []
    buffered_tool_calls: List[RequestToolCall] = []

    def flush_message() -> None:
        if not buffered_parts and not buffered_tool_calls:
            return
        chat_message: JsonDict = {
            "role": message.role,
            "content": _chat_content_from_parts(buffered_parts),
        }
        if buffered_tool_calls:
            chat_message["tool_calls"] = [
                request_tool_call_to_openai_chat_tool_call(tool_call)
                for tool_call in buffered_tool_calls
            ]
        projected.append(chat_message)
        buffered_parts[:] = []
        buffered_tool_calls[:] = []

    for part in message.parts:
        if part.tool_result is not None:
            flush_message()
            projected.append(request_tool_result_to_openai_chat_message(part.tool_result))
            continue
        if part.tool_call is not None:
            buffered_tool_calls.append(part.tool_call)
            continue
        if _part_has_projectable_text(part):
            buffered_parts.append(part)

    flush_message()
    if not projected and not message.parts:
        projected.append({"role": message.role, "content": ""})
    return projected


def request_tool_call_to_openai_chat_tool_call(tool_call: RequestToolCall) -> JsonDict:
    """Project an assistant tool call to Chat Completions ``tool_calls`` shape."""

    return {
        "id": tool_call.call_id,
        "type": "function",
        "function": {
            "name": tool_call.tool_name,
            "arguments": _tool_call_arguments_text(tool_call),
        },
    }


def request_tool_result_to_openai_chat_message(tool_result: RequestToolResult) -> JsonDict:
    """Project a EFP runtime tool result to a Chat Completions tool message."""

    return {
        "role": "tool",
        "tool_call_id": tool_result.call_id,
        "name": tool_result.tool_name,
        "content": _tool_result_content(tool_result),
    }


def request_message_to_openai_responses_input(message: RequestMessage) -> JsonDict:
    """Project one EFP runtime message to a typed Responses input message."""

    content: List[JsonDict] = []
    for part in message.parts:
        content.extend(
            request_part_to_openai_responses_content(part, role=message.role)
        )
    if not content:
        content.append(
            {"type": _responses_text_content_type(message.role), "text": ""}
        )
    return {"role": message.role, "content": content}


def request_part_to_openai_responses_content(
    part: RequestMessagePart,
    *,
    role: str = "",
) -> List[JsonDict]:
    """Project one EFP runtime message part to typed Responses content items."""

    if part.tool_call is not None:
        return [_tool_call_to_responses_item(part.tool_call)]
    if part.tool_result is not None:
        return [_tool_result_to_responses_item(part.tool_result)]
    if part.reasoning is not None:
        return [
            {
                "type": "reasoning",
                "text": part.reasoning.text,
                "metadata": _copy_mapping(part.reasoning.metadata),
            }
        ]
    if _is_image_attachment_part(part):
        return [{"type": "input_image", "image_url": part.attachment.url}]
    if _part_has_projectable_text(part):
        return [
            {
                "type": _responses_text_content_type(role),
                "text": _part_to_text(part),
                "metadata": _content_item_metadata(part),
            }
        ]
    return []


def _responses_text_content_type(role: str) -> str:
    return "output_text" if role == "assistant" else "input_text"


def _tool_call_to_responses_item(tool_call: RequestToolCall) -> JsonDict:
    arguments_text = _tool_call_arguments_text(tool_call)
    return {
        "type": "function_call",
        "call_id": normalize_responses_call_id(tool_call.call_id),
        "name": tool_call.tool_name,
        "tool_name": tool_call.tool_name,
        "arguments": arguments_text,
        "arguments_text": arguments_text,
        "arguments_json": _copy_mapping(tool_call.arguments),
        "status": tool_call.status,
        "call_type": tool_call.call_type,
        "raw": _copy_mapping(tool_call.raw),
        "metadata": _copy_mapping(tool_call.metadata),
        "created_at": tool_call.created_at,
    }


def _tool_result_to_responses_item(tool_result: RequestToolResult) -> JsonDict:
    return {
        "type": "function_call_output",
        "call_id": normalize_responses_call_id(tool_result.call_id),
        "name": tool_result.tool_name,
        "tool_name": tool_result.tool_name,
        "output": _tool_result_content(tool_result),
        "content": tool_result.content,
        "success": tool_result.success,
        "error": tool_result.error,
        "status": tool_result.status,
        "truncated": tool_result.truncated,
        "attachments": [_attachment_metadata(attachment) for attachment in tool_result.attachments],
        "metadata": _copy_mapping(tool_result.metadata),
        "created_at": tool_result.created_at,
    }


def _payload_metadata(
    request: ProviderRequest,
    metadata: Optional[Mapping[str, Any]],
    endpoint: str,
) -> JsonDict:
    combined = _copy_mapping(request.metadata)
    if metadata:
        combined.update(_copy_mapping(metadata))
    combined["efp_projection"] = {
        "provider": "openai",
        "endpoint": endpoint,
        "messages": [_message_trace(index, message) for index, message in enumerate(request.messages)],
        "tools": [_tool_schema_trace(index, schema) for index, schema in enumerate(request.tools)],
    }
    return combined


def _message_trace(index: int, message: RequestMessage) -> JsonDict:
    return {
        "index": index,
        "role": message.role,
        "metadata": _copy_mapping(message.metadata),
        "parts": [_part_trace(part_index, part) for part_index, part in enumerate(message.parts)],
    }


def _part_trace(index: int, part: RequestMessagePart) -> JsonDict:
    trace: JsonDict = {
        "index": index,
        "type": part.type,
        "metadata": _copy_mapping(part.metadata),
    }
    if part.context is not None:
        trace["context"] = {
            "type": part.context.type,
            "text": part.context.text,
            "metadata": _copy_mapping(part.context.metadata),
        }
    if part.attachment is not None:
        trace["attachment"] = _attachment_metadata(part.attachment)
    if part.tool_call is not None:
        trace["tool_call"] = {
            "call_id": part.tool_call.call_id,
            "tool_name": part.tool_call.tool_name,
            "arguments_text": _tool_call_arguments_text(part.tool_call),
            "status": part.tool_call.status,
            "call_type": part.tool_call.call_type,
            "metadata": _copy_mapping(part.tool_call.metadata),
        }
    if part.tool_result is not None:
        trace["tool_result"] = {
            "call_id": part.tool_result.call_id,
            "tool_name": part.tool_result.tool_name,
            "success": part.tool_result.success,
            "status": part.tool_result.status,
            "truncated": part.tool_result.truncated,
            "metadata": _copy_mapping(part.tool_result.metadata),
        }
    return trace


def _tool_schema_trace(index: int, schema: RequestToolSchema) -> JsonDict:
    return {
        "index": index,
        "id": schema.id,
        "name": schema.name,
        "metadata": _copy_mapping(schema.metadata),
    }


def _parts_to_text(parts: List[RequestMessagePart]) -> str:
    chunks = [_part_to_text(part) for part in parts]
    return "\n\n".join(chunk for chunk in chunks if chunk != "")


def _is_image_attachment_part(part: RequestMessagePart) -> bool:
    att = part.attachment
    return (
        att is not None
        and isinstance(getattr(att, "mime_type", None), str)
        and att.mime_type.startswith("image/")
        and bool(getattr(att, "url", None))
    )


def _chat_content_from_parts(parts: List[RequestMessagePart]):
    """Chat Completions content: a plain string, or a list mixing text and
    image_url blocks when any part is an image attachment."""
    if not any(_is_image_attachment_part(part) for part in parts):
        return _parts_to_text(parts)
    items: List[JsonDict] = []
    text_buffer: List[str] = []

    def _flush_text() -> None:
        joined = "\n\n".join(chunk for chunk in text_buffer if chunk != "")
        if joined:
            items.append({"type": "text", "text": joined})
        text_buffer.clear()

    for part in parts:
        if _is_image_attachment_part(part):
            _flush_text()
            items.append({"type": "image_url", "image_url": {"url": part.attachment.url}})
        else:
            text_buffer.append(_part_to_text(part))
    _flush_text()
    return items


def _part_has_projectable_text(part: RequestMessagePart) -> bool:
    return (
        part.text is not None
        or part.context is not None
        or part.attachment is not None
    )


def _part_to_text(part: RequestMessagePart) -> str:
    if part.attachment is not None:
        return _attachment_to_text(part.attachment, part.context)
    if part.context is not None:
        return _context_to_text(part.context)
    return part.text or ""


def _context_to_text(context: RequestContext) -> str:
    lines = ["[context:{0}]".format(context.type or "context")]
    if context.text:
        lines.append(context.text)
    if context.metadata:
        lines.append("metadata: {0}".format(_stable_json(context.metadata)))
    return "\n".join(lines)


def _attachment_to_text(
    attachment: RequestAttachment,
    context: Optional[RequestContext] = None,
) -> str:
    lines = ["[attachment:{0}]".format(attachment.attachment_id)]
    for key, value in _attachment_metadata(attachment).items():
        if key == "attachment_id":
            continue
        if value not in (None, "", {}):
            if key == "metadata":
                lines.append("metadata: {0}".format(_stable_json(value)))
            else:
                lines.append("{0}: {1}".format(key, value))
    if context is not None and context.metadata:
        lines.append("context_metadata: {0}".format(_stable_json(context.metadata)))
    return "\n".join(lines)


def _attachment_metadata(attachment: RequestAttachment) -> JsonDict:
    return {
        "attachment_id": attachment.attachment_id,
        "mime_type": attachment.mime_type,
        "filename": attachment.filename,
        "url": attachment.url,
        "text_ref": attachment.text_ref,
        "metadata": _copy_mapping(attachment.metadata),
        "created_at": attachment.created_at,
    }


def _content_item_metadata(part: RequestMessagePart) -> JsonDict:
    metadata: JsonDict = {
        "part_type": part.type,
        "part_metadata": _copy_mapping(part.metadata),
    }
    if part.context is not None:
        metadata["context"] = {
            "type": part.context.type,
            "metadata": _copy_mapping(part.context.metadata),
        }
    if part.attachment is not None:
        metadata["attachment"] = _attachment_metadata(part.attachment)
    return metadata


def _tool_call_arguments_text(tool_call: RequestToolCall) -> str:
    if tool_call.arguments_text:
        return tool_call.arguments_text
    if not tool_call.arguments:
        return ""
    return _stable_json(tool_call.arguments, compact=True)


def _tool_result_content(tool_result: RequestToolResult) -> str:
    if tool_result.content:
        return tool_result.content
    if tool_result.error:
        return tool_result.error
    return ""


def _copy_mapping(mapping: Mapping[str, Any]) -> JsonDict:
    copied = _copy_value(dict(mapping))
    if isinstance(copied, dict):
        return copied
    return dict(mapping)


def _copy_value(value: Any) -> Any:
    try:
        return deepcopy(value)
    except Exception:
        return value


def _stable_json(value: Any, *, compact: bool = False) -> str:
    separators = (",", ":") if compact else (", ", ": ")
    return json.dumps(
        value,
        sort_keys=True,
        separators=separators,
        ensure_ascii=False,
        default=str,
    )
