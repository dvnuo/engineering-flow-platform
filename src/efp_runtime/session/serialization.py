"""JSON serialization helpers for Runtime v2 sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping

from ..events import RuntimeEvent
from ..types import Attachment, SkillPackage, ToolCall, ToolResult
from .checkpoint import SessionCheckpoint
from .models import CompactionPart, Message, MessagePart, MessagePartType, Session, TaskPart


_TYPE_KEY = "__efp_runtime_type__"
_VALUE_KEY = "value"


def session_to_dict(session: Session) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session.session_id,
        "title": session.title,
        "messages": [message_to_dict(message) for message in session.messages],
        "metadata": _encode_value(session.metadata),
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def session_from_dict(data: Mapping[str, Any]) -> Session:
    session = Session(
        session_id=str(data["session_id"]),
        title=data.get("title"),
        messages=[message_from_dict(item) for item in data.get("messages", [])],
        metadata=_decoded_mapping(data.get("metadata", {})),
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )
    _validate_session_bindings(session)
    return session


def checkpoint_to_dict(checkpoint: SessionCheckpoint) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "checkpoint_id": checkpoint.checkpoint_id,
        "session_id": checkpoint.session_id,
        "message_id": checkpoint.message_id,
        "message_count": checkpoint.message_count,
        "label": checkpoint.label,
        "metadata": _encode_value(checkpoint.metadata),
        "created_at": checkpoint.created_at,
    }


def checkpoint_from_dict(data: Mapping[str, Any]) -> SessionCheckpoint:
    return SessionCheckpoint(
        checkpoint_id=str(data["checkpoint_id"]),
        session_id=str(data["session_id"]),
        message_id=data.get("message_id"),
        message_count=int(data["message_count"]),
        label=data.get("label"),
        metadata=_decoded_mapping(data.get("metadata", {})),
        created_at=str(data["created_at"]),
    )


def message_to_dict(message: Message) -> Dict[str, Any]:
    return {
        "role": message.role.value,
        "session_id": message.session_id,
        "message_id": message.message_id,
        "parts": [part_to_dict(part) for part in message.parts],
        "parent_message_id": message.parent_message_id,
        "metadata": _encode_value(message.metadata),
        "status": message.status,
        "usage": _encode_value(message.usage),
        "created_at": message.created_at,
        "completed_at": message.completed_at,
    }


def message_from_dict(data: Mapping[str, Any]) -> Message:
    return Message(
        role=str(data["role"]),
        session_id=str(data.get("session_id") or ""),
        message_id=str(data["message_id"]),
        parts=[part_from_dict(item) for item in data.get("parts", [])],
        parent_message_id=data.get("parent_message_id"),
        metadata=_decoded_mapping(data.get("metadata", {})),
        status=str(data.get("status", "pending")),
        usage=_decoded_mapping(data.get("usage", {})),
        created_at=str(data["created_at"]),
        completed_at=data.get("completed_at"),
    )


def part_to_dict(part: MessagePart) -> Dict[str, Any]:
    return {
        "type": part.type.value,
        "part_id": part.part_id,
        "session_id": part.session_id,
        "message_id": part.message_id,
        "text": part.text,
        "reasoning": part.reasoning,
        "tool_call": _tool_call_to_dict(part.tool_call) if part.tool_call else None,
        "tool_result": _tool_result_to_dict(part.tool_result) if part.tool_result else None,
        "compaction": _compaction_to_dict(part.compaction) if part.compaction else None,
        "task": _task_to_dict(part.task) if part.task else None,
        "attachment": _attachment_to_dict(part.attachment) if part.attachment else None,
        "metadata": _encode_value(part.metadata),
        "created_at": part.created_at,
    }


def part_from_dict(data: Mapping[str, Any]) -> MessagePart:
    part_type = MessagePartType(str(data["type"]))
    kwargs: Dict[str, Any] = {
        "type": part_type,
        "part_id": str(data["part_id"]),
        "session_id": data.get("session_id"),
        "message_id": data.get("message_id"),
        "text": data.get("text"),
        "reasoning": data.get("reasoning"),
        "metadata": _decoded_mapping(data.get("metadata", {})),
        "created_at": str(data["created_at"]),
    }
    if part_type is MessagePartType.TOOL_CALL:
        kwargs["tool_call"] = _tool_call_from_dict(data["tool_call"])
    elif part_type is MessagePartType.TOOL_RESULT:
        kwargs["tool_result"] = _tool_result_from_dict(data["tool_result"])
    elif part_type is MessagePartType.COMPACTION:
        kwargs["compaction"] = _compaction_from_dict(data["compaction"])
    elif part_type is MessagePartType.TASK:
        kwargs["task"] = _task_from_dict(data["task"])
    elif part_type is MessagePartType.ATTACHMENT:
        kwargs["attachment"] = _attachment_from_dict(data["attachment"])
    return MessagePart(**kwargs)


def _tool_call_to_dict(tool_call: ToolCall) -> Dict[str, Any]:
    return {
        "tool_name": tool_call.tool_name,
        "arguments": _encode_value(tool_call.arguments),
        "call_id": tool_call.call_id,
        "status": tool_call.status,
        "arguments_text": tool_call.arguments_text,
        "call_type": tool_call.call_type,
        "raw": _encode_value(tool_call.raw),
        "metadata": _encode_value(tool_call.metadata),
        "created_at": tool_call.created_at,
    }


def _tool_call_from_dict(data: Mapping[str, Any]) -> ToolCall:
    decoded = _decode_value(data)
    if isinstance(decoded, ToolCall):
        return decoded
    return ToolCall(
        tool_name=str(decoded["tool_name"]),
        arguments=_decoded_mapping(decoded.get("arguments", {})),
        call_id=str(decoded["call_id"]),
        status=str(decoded.get("status", "pending")),
        arguments_text=str(decoded.get("arguments_text", "")),
        type=str(decoded.get("call_type", decoded.get("type", "function"))),
        raw=_decoded_mapping(decoded.get("raw", {})),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
        created_at=str(decoded["created_at"]),
    )


def _tool_result_to_dict(tool_result: ToolResult) -> Dict[str, Any]:
    return {
        "call_id": tool_result.call_id,
        "tool_name": tool_result.tool_name,
        "output": _encode_value(tool_result.output),
        "success": tool_result.success,
        "error": tool_result.error,
        "content": tool_result.content,
        "status": tool_result.status,
        "attachments": [_attachment_to_dict(item) for item in tool_result.attachments],
        "metadata": _encode_value(tool_result.metadata),
        "truncated": tool_result.truncated,
        "events": _encode_value(tool_result.events),
        "created_at": tool_result.created_at,
    }


def _tool_result_from_dict(data: Mapping[str, Any]) -> ToolResult:
    decoded = _decode_value(data)
    if isinstance(decoded, ToolResult):
        return decoded
    return ToolResult(
        call_id=str(decoded["call_id"]),
        tool_name=str(decoded["tool_name"]),
        output=_decode_value(decoded.get("output")),
        success=bool(decoded.get("success", False)),
        error=decoded.get("error"),
        content=decoded.get("content"),
        status=str(decoded.get("status", "success")),
        attachments=[_attachment_from_dict(item) for item in decoded.get("attachments", [])],
        metadata=_decoded_mapping(decoded.get("metadata", {})),
        truncated=bool(decoded.get("truncated", False)),
        events=_decode_value(decoded.get("events", [])),
        created_at=str(decoded["created_at"]),
    )


def _attachment_to_dict(attachment: Attachment) -> Dict[str, Any]:
    return {
        "attachment_id": attachment.attachment_id,
        "mime_type": attachment.mime_type,
        "filename": attachment.filename,
        "url": attachment.url,
        "text_ref": attachment.text_ref,
        "metadata": _encode_value(attachment.metadata),
        "created_at": attachment.created_at,
    }


def _attachment_from_dict(data: Mapping[str, Any]) -> Attachment:
    decoded = _decode_value(data)
    if isinstance(decoded, Attachment):
        return decoded
    return Attachment(
        attachment_id=str(decoded["attachment_id"]),
        mime_type=str(decoded["mime_type"]),
        filename=decoded.get("filename"),
        url=decoded.get("url"),
        text_ref=decoded.get("text_ref"),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
        created_at=str(decoded["created_at"]),
    )


def _skill_package_to_dict(skill: SkillPackage) -> Dict[str, Any]:
    return {
        "name": skill.name,
        "content": skill.content,
        "location": skill.location,
        "description": skill.description,
        "root": str(skill.root),
        "skill_file": str(skill.skill_file),
        "sidecar_files": [str(path) for path in skill.sidecar_files],
        "metadata": _encode_value(skill.metadata),
        "loaded_at": skill.loaded_at,
    }


def _skill_package_from_dict(data: Mapping[str, Any]) -> SkillPackage:
    decoded = _decode_value(data)
    if isinstance(decoded, SkillPackage):
        return decoded
    return SkillPackage(
        name=str(decoded["name"]),
        content=str(decoded["content"]),
        location=decoded.get("location"),
        description=decoded.get("description"),
        root=decoded.get("root"),
        skill_file=decoded.get("skill_file"),
        sidecar_files=decoded.get("sidecar_files", []),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
        loaded_at=str(decoded["loaded_at"]),
    )


def _runtime_event_to_dict(event: RuntimeEvent) -> Dict[str, Any]:
    return {
        "type": event.type,
        "message": event.message,
        "session_id": event.session_id,
        "message_id": event.message_id,
        "part_id": event.part_id,
        "payload": _encode_value(event.payload),
        "metadata": _encode_value(event.metadata),
        "created_at": event.created_at,
    }


def _runtime_event_from_dict(data: Mapping[str, Any]) -> RuntimeEvent:
    decoded = _decode_value(data)
    if isinstance(decoded, RuntimeEvent):
        return decoded
    return RuntimeEvent(
        type=str(decoded["type"]),
        message=str(decoded.get("message", "")),
        session_id=decoded.get("session_id"),
        message_id=decoded.get("message_id"),
        part_id=decoded.get("part_id"),
        payload=_decoded_mapping(decoded.get("payload", {})),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
        created_at=str(decoded["created_at"]),
    )


def _compaction_to_dict(compaction: CompactionPart) -> Dict[str, Any]:
    return {
        "summary": compaction.summary,
        "source_message_ids": list(compaction.source_message_ids),
        "auto": compaction.auto,
        "overflow": compaction.overflow,
        "tail_start_message_id": compaction.tail_start_message_id,
        "original_part_count": compaction.original_part_count,
        "original_message_count": compaction.original_message_count,
        "tool_pair_count": compaction.tool_pair_count,
        "metadata": _encode_value(compaction.metadata),
    }


def _compaction_from_dict(data: Mapping[str, Any]) -> CompactionPart:
    decoded = _decode_value(data)
    if isinstance(decoded, CompactionPart):
        return decoded
    return CompactionPart(
        summary=str(decoded["summary"]),
        source_message_ids=[str(item) for item in decoded.get("source_message_ids", [])],
        auto=bool(decoded.get("auto", False)),
        overflow=decoded.get("overflow"),
        tail_start_message_id=decoded.get("tail_start_message_id"),
        original_part_count=int(decoded.get("original_part_count", 0)),
        original_message_count=int(decoded.get("original_message_count", 0)),
        tool_pair_count=int(decoded.get("tool_pair_count", 0)),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
    )


def _task_to_dict(task: TaskPart) -> Dict[str, Any]:
    return {
        "prompt": task.prompt,
        "task_id": task.task_id,
        "description": task.description,
        "status": task.status,
        "agent": task.agent,
        "model": task.model,
        "metadata": _encode_value(task.metadata),
    }


def _task_from_dict(data: Mapping[str, Any]) -> TaskPart:
    decoded = _decode_value(data)
    if isinstance(decoded, TaskPart):
        return decoded
    return TaskPart(
        prompt=str(decoded["prompt"]),
        task_id=str(decoded["task_id"]),
        description=decoded.get("description"),
        status=str(decoded.get("status", "pending")),
        agent=decoded.get("agent"),
        model=decoded.get("model"),
        metadata=_decoded_mapping(decoded.get("metadata", {})),
    )


def _encode_value(value: Any) -> Any:
    if isinstance(value, ToolCall):
        return {_TYPE_KEY: "ToolCall", _VALUE_KEY: _tool_call_to_dict(value)}
    if isinstance(value, ToolResult):
        return {_TYPE_KEY: "ToolResult", _VALUE_KEY: _tool_result_to_dict(value)}
    if isinstance(value, Attachment):
        return {_TYPE_KEY: "Attachment", _VALUE_KEY: _attachment_to_dict(value)}
    if isinstance(value, SkillPackage):
        return {_TYPE_KEY: "SkillPackage", _VALUE_KEY: _skill_package_to_dict(value)}
    if isinstance(value, RuntimeEvent):
        return {_TYPE_KEY: "RuntimeEvent", _VALUE_KEY: _runtime_event_to_dict(value)}
    if isinstance(value, CompactionPart):
        return {_TYPE_KEY: "CompactionPart", _VALUE_KEY: _compaction_to_dict(value)}
    if isinstance(value, TaskPart):
        return {_TYPE_KEY: "TaskPart", _VALUE_KEY: _task_to_dict(value)}
    if isinstance(value, Path):
        return {_TYPE_KEY: "Path", _VALUE_KEY: str(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _encode_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_encode_value(item) for item in value), key=repr)
    if is_dataclass(value):
        return _encode_value(asdict(value))
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _decode_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_value(item) for item in value]
    if not isinstance(value, dict):
        return value

    type_name = value.get(_TYPE_KEY)
    if type_name == "ToolCall" and _VALUE_KEY in value:
        return _tool_call_from_dict(value[_VALUE_KEY])
    if type_name == "ToolResult" and _VALUE_KEY in value:
        return _tool_result_from_dict(value[_VALUE_KEY])
    if type_name == "Attachment" and _VALUE_KEY in value:
        return _attachment_from_dict(value[_VALUE_KEY])
    if type_name == "SkillPackage" and _VALUE_KEY in value:
        return _skill_package_from_dict(value[_VALUE_KEY])
    if type_name == "RuntimeEvent" and _VALUE_KEY in value:
        return _runtime_event_from_dict(value[_VALUE_KEY])
    if type_name == "CompactionPart" and _VALUE_KEY in value:
        return _compaction_from_dict(value[_VALUE_KEY])
    if type_name == "TaskPart" and _VALUE_KEY in value:
        return _task_from_dict(value[_VALUE_KEY])
    if type_name == "Path" and _VALUE_KEY in value:
        return Path(str(value[_VALUE_KEY]))
    return {str(key): _decode_value(item) for key, item in value.items()}


def _decoded_mapping(value: Any) -> Dict[str, Any]:
    decoded = _decode_value(value)
    if isinstance(decoded, dict):
        return dict(decoded)
    raise TypeError("expected JSON object")


def _validate_session_bindings(session: Session) -> None:
    for message in session.messages:
        if message.session_id in (None, ""):
            message.session_id = session.session_id
        elif message.session_id != session.session_id:
            raise ValueError(
                f"message session mismatch: expected {session.session_id}, got {message.session_id}"
            )
        for part in message.parts:
            if part.session_id in (None, ""):
                part.session_id = session.session_id
            elif part.session_id != session.session_id:
                raise ValueError(
                    f"part session mismatch: expected {session.session_id}, got {part.session_id}"
                )
            if part.message_id in (None, ""):
                part.message_id = message.message_id
            elif part.message_id != message.message_id:
                raise ValueError(
                    f"part message mismatch: expected {message.message_id}, got {part.message_id}"
                )


__all__ = [
    "checkpoint_from_dict",
    "checkpoint_to_dict",
    "message_from_dict",
    "message_to_dict",
    "part_from_dict",
    "part_to_dict",
    "session_from_dict",
    "session_to_dict",
]
