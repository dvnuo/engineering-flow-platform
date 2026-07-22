"""Session store protocol for EFP runtime."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple, Union

from .checkpoint import SessionCheckpoint
from .models import Message, MessagePart, MessageRole, Session


ToolPair = Tuple[MessagePart, Optional[MessagePart]]


class SessionStore(Protocol):
    def create_session(
        self,
        *,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Session:
        ...

    def get_session(self, session_id: str) -> Session:
        ...

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        replace_metadata: bool = False,
    ) -> Session:
        ...

    def list_sessions(self) -> list[Session]:
        ...

    def get_session_summary(self, session_id: str) -> Any:
        ...

    def list_session_summaries(self) -> list[Any]:
        ...

    def delete_session(self, session_id: str) -> bool:
        ...

    def fork_session(
        self,
        session_id: str,
        *,
        message_id: Optional[str] = None,
        new_session_id: Optional[str] = None,
    ) -> Session:
        ...

    def append_message(
        self,
        session_id: str,
        *,
        role: Union[MessageRole, str],
        parts: Optional[Iterable[MessagePart]] = None,
        message_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        status: str = "pending",
        usage: Optional[dict] = None,
        completed_at: Optional[str] = None,
    ) -> Message:
        ...

    def append_part(self, session_id: str, message_id: str, part: MessagePart) -> MessagePart:
        ...

    def read_history(self, session_id: str) -> List[Message]:
        ...

    def replace_history(self, session_id: str, messages: Iterable[Message]) -> Session:
        ...

    def tool_pairs(self, session_id: str) -> Dict[str, ToolPair]:
        ...

    def create_checkpoint(
        self,
        session_id: str,
        *,
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
        checkpoint_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> SessionCheckpoint:
        ...

    def list_checkpoints(self, session_id: str) -> List[SessionCheckpoint]:
        ...

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Session:
        ...

    def delete_checkpoint(self, session_id: str, checkpoint_id: str) -> bool:
        ...


__all__ = ["SessionStore", "ToolPair"]
