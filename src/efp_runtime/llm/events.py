"""LLM event model for EFP Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Union

from ..types import ToolCall, ToolResult


class LLMEventType(str, Enum):
    MESSAGE_START = "message_start"
    TEXT_START = "text_start"
    TEXT_DELTA = "text_delta"
    TEXT_END = "text_end"
    REASONING_DELTA = "reasoning_delta"
    TOOL_INPUT_START = "tool_input_start"
    TOOL_INPUT_DELTA = "tool_input_delta"
    TOOL_INPUT_END = "tool_input_end"
    TOOL_CALL_COMPLETE = "tool_call_complete"
    TOOL_RESULT = "tool_result"
    STEP_START = "step_start"
    STEP_FINISH = "step_finish"
    ERROR = "error"


@dataclass
class LLMEvent:
    """A normalized provider event consumed by the session processor."""

    type: Union[LLMEventType, str]
    message_id: Optional[str] = None
    part_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    delta: str = ""
    text: str = ""
    tool_call: Optional[ToolCall] = None
    tool_result: Optional[ToolResult] = None
    usage: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def type_value(self) -> str:
        return self.type.value if isinstance(self.type, LLMEventType) else str(self.type)


def coerce_event_type(value: Union[LLMEventType, str]) -> LLMEventType:
    if isinstance(value, LLMEventType):
        return value
    return LLMEventType(str(value))
