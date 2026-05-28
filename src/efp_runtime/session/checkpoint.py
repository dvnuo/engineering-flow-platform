"""Checkpoint metadata for Runtime v2 session history snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from ..types import utc_now_iso


@dataclass
class SessionCheckpoint:
    checkpoint_id: str
    session_id: str
    message_id: Optional[str]
    message_count: int
    label: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        self.metadata = dict(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "message_count": self.message_count,
            "label": self.label,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SessionCheckpoint":
        return cls(
            checkpoint_id=str(data["checkpoint_id"]),
            session_id=str(data["session_id"]),
            message_id=data.get("message_id"),
            message_count=int(data["message_count"]),
            label=data.get("label"),
            metadata=dict(data.get("metadata", {})),
            created_at=str(data["created_at"]),
        )


__all__ = ["SessionCheckpoint"]
