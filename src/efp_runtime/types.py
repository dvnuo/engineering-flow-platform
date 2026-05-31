"""Shared data contracts for EFP runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import uuid


JsonObject = Dict[str, Any]
_MISSING = object()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


@dataclass
class Attachment:
    mime_type: str
    attachment_id: str = field(default_factory=lambda: new_id("att"))
    filename: Optional[str] = None
    url: Optional[str] = None
    text_ref: Optional[str] = None
    metadata: JsonObject = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "url": self.url,
            "text_ref": self.text_ref,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


@dataclass(init=False)
class ToolCall:
    """A normalized tool call.

    Canonical fields are ``tool_name``, ``arguments``, and ``call_id``. Aliases
    are accepted for compatibility with provider and tool-runtime naming:
    ``name``/``tool_id`` map to ``tool_name`` and ``id`` maps to ``call_id``.
    """

    tool_name: str
    arguments: JsonObject
    call_id: str
    status: str
    arguments_text: str
    call_type: str
    raw: JsonObject
    metadata: JsonObject
    created_at: str

    def __init__(
        self,
        tool_name: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        call_id: Optional[str] = None,
        *,
        name: Optional[str] = None,
        tool_id: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        id: Optional[str] = None,
        status: str = "pending",
        arguments_text: Optional[str] = None,
        type: str = "function",
        raw: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        resolved_name = tool_name or name or tool_id
        if not resolved_name:
            raise ValueError("tool_name is required")

        if arguments is None:
            arguments = dict(args or {})
        else:
            arguments = dict(arguments)

        if arguments_text is None:
            arguments_text = _json_text(arguments)

        self.tool_name = str(resolved_name)
        self.arguments = arguments
        self.call_id = str(call_id or id or new_id("call"))
        self.status = status
        self.arguments_text = arguments_text
        self.call_type = type
        self.raw = dict(raw or {})
        self.metadata = dict(metadata or {})
        self.created_at = created_at or utc_now_iso()

    @property
    def id(self) -> str:
        return self.call_id

    @property
    def name(self) -> str:
        return self.tool_name

    @property
    def tool_id(self) -> str:
        return self.tool_name

    @property
    def args(self) -> JsonObject:
        return self.arguments

    @property
    def type(self) -> str:
        return self.call_type


@dataclass(init=False)
class ToolResult:
    """A normalized result for one tool call."""

    call_id: str
    tool_name: str
    output: Any
    success: bool
    error: Optional[str]
    content: str
    status: str
    attachments: List[Attachment]
    metadata: JsonObject
    truncated: bool
    events: List[Any]
    created_at: str

    def __init__(
        self,
        call_id: str,
        tool_name: Optional[str] = None,
        *,
        tool_id: Optional[str] = None,
        name: Optional[str] = None,
        output: Any = None,
        success: Optional[bool] = None,
        is_error: Optional[bool] = None,
        error: Optional[str] = None,
        content: Optional[str] = None,
        status: Optional[str] = None,
        attachments: Optional[Iterable[Attachment]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        truncated: bool = False,
        events: Optional[Iterable[Any]] = None,
        created_at: Optional[str] = None,
    ) -> None:
        resolved_name = tool_name or name or tool_id
        if not resolved_name:
            raise ValueError("tool_name is required")

        if status is None:
            if is_error or error or success is False:
                status = "error"
            else:
                status = "success"
        if success is None:
            success = status == "success"

        self.call_id = str(call_id)
        self.tool_name = str(resolved_name)
        self.output = output
        self.success = bool(success)
        self.error = error
        self.content = _result_content(content, output, error)
        self.status = status
        self.attachments = list(attachments or [])
        self.metadata = dict(metadata or {})
        self.truncated = truncated
        self.events = list(events or [])
        self.created_at = created_at or utc_now_iso()

    @property
    def tool_id(self) -> str:
        return self.tool_name

    @property
    def name(self) -> str:
        return self.tool_name

    @property
    def is_error(self) -> bool:
        return not self.success

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "success": self.success,
            "content": self.content,
            "output": self.output,
            "error": self.error,
            "metadata": dict(self.metadata),
            "truncated": self.truncated,
            "events": [
                event.to_dict() if hasattr(event, "to_dict") else event for event in self.events
            ],
            "attachments": [attachment.to_dict() for attachment in self.attachments],
            "created_at": self.created_at,
        }


@dataclass(init=False)
class SkillPackage:
    """A discovered skill package rooted at a SKILL.md/skill.md file."""

    name: str
    content: str
    location: str
    description: str
    root: Path
    skill_file: Path
    sidecar_files: List[Path]
    metadata: JsonObject
    loaded_at: str

    def __init__(
        self,
        name: str,
        content: str,
        location: Optional[str] = None,
        *,
        description: Optional[str] = None,
        root: Optional[str | Path] = None,
        skill_file: Optional[str | Path] = None,
        sidecar_files: Optional[Iterable[str | Path]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        loaded_at: Optional[str] = None,
    ) -> None:
        resolved_skill_file = Path(skill_file or location or "")
        resolved_root = Path(root) if root is not None else resolved_skill_file.parent
        self.name = name
        self.content = content
        self.location = str(location or resolved_skill_file)
        self.description = description or ""
        self.root = resolved_root
        self.skill_file = resolved_skill_file
        self.sidecar_files = [Path(path) for path in sidecar_files or []]
        self.metadata = dict(metadata or {})
        self.loaded_at = loaded_at or utc_now_iso()


def _json_text(value: Any) -> str:
    if value in (None, {}):
        return ""
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def _result_content(content: Optional[str], output: Any, error: Optional[str]) -> str:
    if content is not None:
        return content
    if error:
        return error
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, sort_keys=True, default=str)
    except TypeError:
        return str(output)
