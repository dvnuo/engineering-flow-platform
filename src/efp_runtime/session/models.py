"""Session history models for EFP runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from ..types import Attachment, ToolCall, ToolResult, new_id, utc_now_iso


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class MessagePartType(str, Enum):
    TEXT = "text"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    TASK = "task"
    ATTACHMENT = "attachment"
    ERROR = "error"


@dataclass
class CompactionPart:
    summary: str
    source_message_ids: List[str] = field(default_factory=list)
    auto: bool = False
    overflow: Optional[bool] = None
    tail_start_message_id: Optional[str] = None
    original_part_count: int = 0
    original_message_count: int = 0
    tool_pair_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source_message_ids = list(self.source_message_ids)
        self.metadata = dict(self.metadata)


@dataclass
class TaskPart:
    prompt: str
    task_id: str = field(default_factory=lambda: new_id("task"))
    description: Optional[str] = None
    status: str = "pending"
    agent: Optional[str] = None
    model: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)


@dataclass
class MessagePart:
    type: MessagePartType
    part_id: str = field(default_factory=lambda: new_id("part"))
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    text: Optional[str] = None
    reasoning: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    compaction: Optional[CompactionPart] = None
    task: Optional[TaskPart] = None
    attachment: Optional[Attachment] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if not isinstance(self.type, MessagePartType):
            self.type = MessagePartType(self.type)
        self.metadata = dict(self.metadata)
        self._validate_payload()

    @property
    def id(self) -> str:
        return self.part_id

    @property
    def data(self) -> Dict[str, Any]:
        return self.metadata

    @classmethod
    def text_part(cls, text: str, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.TEXT, text=text, **kwargs)

    @classmethod
    def reasoning_part(cls, text: str, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.REASONING, reasoning=text, **kwargs)

    @classmethod
    def tool_call_part(cls, tool_call: ToolCall, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.TOOL_CALL, tool_call=tool_call, **kwargs)

    @classmethod
    def tool_result_part(cls, tool_result: ToolResult, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.TOOL_RESULT, tool_result=tool_result, **kwargs)

    @classmethod
    def compaction_part(cls, compaction: CompactionPart, **kwargs: Any) -> "MessagePart":
        return cls(
            type=MessagePartType.COMPACTION,
            compaction=compaction,
            text=compaction.summary,
            **kwargs,
        )

    @classmethod
    def task_part(cls, task: TaskPart, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.TASK, task=task, **kwargs)

    @classmethod
    def attachment_part(cls, attachment: Attachment, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.ATTACHMENT, attachment=attachment, **kwargs)

    @classmethod
    def error_part(cls, text: str, **kwargs: Any) -> "MessagePart":
        return cls(type=MessagePartType.ERROR, text=text, **kwargs)

    def _validate_payload(self) -> None:
        if self.type is MessagePartType.TEXT and self.text is None:
            raise ValueError("text parts require text")
        if self.type is MessagePartType.REASONING and self.reasoning is None:
            raise ValueError("reasoning parts require reasoning text")
        if self.type is MessagePartType.TOOL_CALL and self.tool_call is None:
            raise ValueError("tool_call parts require a ToolCall")
        if self.type is MessagePartType.TOOL_RESULT and self.tool_result is None:
            raise ValueError("tool_result parts require a ToolResult")
        if self.type is MessagePartType.COMPACTION and self.compaction is None:
            raise ValueError("compaction parts require a CompactionPart")
        if self.type is MessagePartType.TASK and self.task is None:
            raise ValueError("task parts require a TaskPart")
        if self.type is MessagePartType.ATTACHMENT and self.attachment is None:
            raise ValueError("attachment parts require an Attachment")
        if self.type is MessagePartType.ERROR and self.text is None:
            raise ValueError("error parts require text")


@dataclass
class Message:
    role: MessageRole
    session_id: str = ""
    message_id: str = field(default_factory=lambda: new_id("msg"))
    parts: List[MessagePart] = field(default_factory=list)
    parent_message_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    usage: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            self.role = MessageRole(self.role)
        self.parts = list(self.parts)
        self.metadata = dict(self.metadata)
        self.usage = dict(self.usage)
        for part in self.parts:
            self._bind_part(part)

    @property
    def id(self) -> str:
        return self.message_id

    @classmethod
    def from_text(
        cls,
        role: MessageRole | str,
        text: str,
        *,
        session_id: str = "",
        message_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Message":
        parts = [MessagePart.text_part(text)] if text else []
        return cls(
            role=role,
            session_id=session_id,
            message_id=message_id or new_id("msg"),
            parts=parts,
            metadata=dict(metadata or {}),
        )

    def append_part(self, part: MessagePart) -> MessagePart:
        self._bind_part(part)
        self.parts.append(part)
        return part

    def extend_parts(self, parts: Iterable[MessagePart]) -> None:
        for part in parts:
            self.append_part(part)

    def _bind_part(self, part: MessagePart) -> None:
        if part.session_id not in (None, "", self.session_id):
            raise ValueError(
                f"part session mismatch: expected {self.session_id}, got {part.session_id}"
            )
        if part.message_id not in (None, "", self.message_id):
            raise ValueError(
                f"part message mismatch: expected {self.message_id}, got {part.message_id}"
            )
        part.session_id = self.session_id
        part.message_id = self.message_id


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: new_id("session"))
    title: Optional[str] = None
    messages: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.messages = list(self.messages)
        self.metadata = dict(self.metadata)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()
