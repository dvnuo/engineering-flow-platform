"""EFP runtime event contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .types import utc_now_iso


@dataclass
class RuntimeEvent:
    type: str
    message: str = ""
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    part_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.payload = dict(self.payload)
        self.metadata = dict(self.metadata)

    @property
    def data(self) -> Dict[str, Any]:
        return self.payload

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "message": self.message,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "part_id": self.part_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }


from .llm.events import LLMEvent, LLMEventType  # noqa: E402

__all__ = ["LLMEvent", "LLMEventType", "RuntimeEvent"]
