"""In-memory session store for EFP Runtime v2."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Dict, Iterable, List, Optional, Tuple

from ..types import new_id
from .checkpoint import SessionCheckpoint
from .models import Message, MessagePart, MessagePartType, MessageRole, Session


ToolPair = Tuple[MessagePart, Optional[MessagePart]]


class InMemorySessionStore:
    """Small deterministic store for runtime v2 contract tests and prototypes."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._checkpoints: Dict[str, Dict[str, Tuple[SessionCheckpoint, Session]]] = {}
        self._lock = RLock()

    def create_session(
        self,
        *,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> Session:
        with self._lock:
            session = Session(
                session_id=session_id or new_id("session"),
                title=title,
                metadata=dict(metadata or {}),
            )
            if session.session_id in self._sessions:
                raise ValueError(f"session already exists: {session.session_id}")
            self._sessions[session.session_id] = session
            return deepcopy(session)

    def get_session(self, session_id: str) -> Session:
        with self._lock:
            return deepcopy(self._require_session(session_id))

    def append_message(
        self,
        session_id: str,
        *,
        role: MessageRole | str,
        parts: Optional[Iterable[MessagePart]] = None,
        message_id: Optional[str] = None,
        parent_message_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        status: str = "pending",
        usage: Optional[dict] = None,
        completed_at: Optional[str] = None,
    ) -> Message:
        with self._lock:
            session = self._require_session(session_id)
            message = Message(
                role=role,
                session_id=session.session_id,
                message_id=message_id or new_id("msg"),
                parent_message_id=parent_message_id,
                metadata=dict(metadata or {}),
                status=status,
                usage=dict(usage or {}),
                completed_at=completed_at,
            )
            session.messages.append(message)
            for part in parts or []:
                self._append_part_locked(session, message, part)
            session.touch()
            return deepcopy(message)

    def append_part(self, session_id: str, message_id: str, part: MessagePart) -> MessagePart:
        with self._lock:
            session = self._require_session(session_id)
            message = self._require_message(session, message_id)
            stored_part = self._append_part_locked(session, message, part)
            session.touch()
            return deepcopy(stored_part)

    def read_history(self, session_id: str) -> List[Message]:
        with self._lock:
            session = self._require_session(session_id)
            return deepcopy(session.messages)

    def tool_pairs(self, session_id: str) -> Dict[str, ToolPair]:
        with self._lock:
            session = self._require_session(session_id)
            pairs: Dict[str, ToolPair] = {}
            for message in session.messages:
                for part in message.parts:
                    if part.type is MessagePartType.TOOL_CALL and part.tool_call:
                        existing = pairs.get(part.tool_call.call_id)
                        pairs[part.tool_call.call_id] = (part, existing[1] if existing else None)
                    elif part.type is MessagePartType.TOOL_RESULT and part.tool_result:
                        call_part = self._find_tool_call_part(session, part.tool_result.call_id)
                        if call_part:
                            pairs[part.tool_result.call_id] = (call_part, part)
            return deepcopy(pairs)

    def create_checkpoint(
        self,
        session_id: str,
        *,
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
        checkpoint_id: Optional[str] = None,
        message_id: Optional[str] = None,
    ) -> SessionCheckpoint:
        with self._lock:
            session = self._require_session(session_id)
            snapshot = self._snapshot_session(session, message_id=message_id)
            resolved_checkpoint_id = checkpoint_id or new_id("checkpoint")
            checkpoints = self._checkpoints.setdefault(session.session_id, {})
            if resolved_checkpoint_id in checkpoints:
                raise ValueError(f"checkpoint already exists: {resolved_checkpoint_id}")

            checkpoint = SessionCheckpoint(
                checkpoint_id=resolved_checkpoint_id,
                session_id=session.session_id,
                message_id=message_id,
                message_count=len(snapshot.messages),
                label=label,
                metadata=dict(metadata or {}),
            )
            checkpoints[checkpoint.checkpoint_id] = (
                deepcopy(checkpoint),
                deepcopy(snapshot),
            )
            return deepcopy(checkpoint)

    def list_checkpoints(self, session_id: str) -> List[SessionCheckpoint]:
        with self._lock:
            self._require_session(session_id)
            checkpoints = self._checkpoints.get(session_id, {})
            ordered = sorted(
                (checkpoint for checkpoint, _snapshot in checkpoints.values()),
                key=lambda checkpoint: (checkpoint.created_at, checkpoint.checkpoint_id),
            )
            return deepcopy(ordered)

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Session:
        with self._lock:
            self._require_session(session_id)
            checkpoints = self._checkpoints.get(session_id, {})
            try:
                _checkpoint, snapshot = checkpoints[checkpoint_id]
            except KeyError as exc:
                raise KeyError(f"unknown checkpoint: {checkpoint_id}") from exc

            restored = deepcopy(snapshot)
            restored.session_id = session_id
            self._rebind_session(restored)
            self._sessions[session_id] = restored
            return deepcopy(restored)

    def delete_checkpoint(self, session_id: str, checkpoint_id: str) -> bool:
        with self._lock:
            self._require_session(session_id)
            checkpoints = self._checkpoints.get(session_id, {})
            if checkpoint_id not in checkpoints:
                return False
            del checkpoints[checkpoint_id]
            if not checkpoints:
                self._checkpoints.pop(session_id, None)
            return True

    def _append_part_locked(
        self,
        session: Session,
        message: Message,
        part: MessagePart,
    ) -> MessagePart:
        stored_part = deepcopy(part)
        if stored_part.session_id not in (None, "", session.session_id):
            raise ValueError(
                f"part session mismatch: expected {session.session_id}, got {stored_part.session_id}"
            )
        if stored_part.message_id not in (None, "", message.message_id):
            raise ValueError(
                f"part message mismatch: expected {message.message_id}, got {stored_part.message_id}"
            )
        if self._find_part(session, stored_part.part_id) is not None:
            raise ValueError(f"part already exists: {stored_part.part_id}")

        stored_part.session_id = session.session_id
        stored_part.message_id = message.message_id
        self._validate_tool_result_pair(session, stored_part)
        message.parts.append(stored_part)
        return stored_part

    def _validate_tool_result_pair(self, session: Session, part: MessagePart) -> None:
        if part.type is not MessagePartType.TOOL_RESULT or not part.tool_result:
            return

        call_part = self._find_tool_call_part(session, part.tool_result.call_id)
        if call_part is None or call_part.tool_call is None:
            raise ValueError(f"tool result has no matching tool call: {part.tool_result.call_id}")

        if call_part.tool_call.tool_name != part.tool_result.tool_name:
            raise ValueError(
                "tool result tool name mismatch: "
                f"call {call_part.tool_call.tool_name}, result {part.tool_result.tool_name}"
            )

        if self._find_tool_result_part(session, part.tool_result.call_id) is not None:
            raise ValueError(f"tool result already exists: {part.tool_result.call_id}")

    def _require_session(self, session_id: str) -> Session:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise KeyError(f"unknown session: {session_id}") from exc

    def _require_message(self, session: Session, message_id: str) -> Message:
        for message in session.messages:
            if message.message_id == message_id:
                return message
        raise KeyError(f"unknown message: {message_id}")

    def _snapshot_session(self, session: Session, *, message_id: Optional[str]) -> Session:
        snapshot = deepcopy(session)
        if message_id is not None:
            message_index = self._message_index(snapshot, message_id)
            snapshot.messages = snapshot.messages[: message_index + 1]
        self._rebind_session(snapshot)
        return snapshot

    def _message_index(self, session: Session, message_id: str) -> int:
        for index, message in enumerate(session.messages):
            if message.message_id == message_id:
                return index
        raise KeyError(f"unknown message: {message_id}")

    def _find_part(self, session: Session, part_id: str) -> Optional[MessagePart]:
        for message in session.messages:
            for part in message.parts:
                if part.part_id == part_id:
                    return part
        return None

    def _find_tool_call_part(self, session: Session, call_id: str) -> Optional[MessagePart]:
        for message in session.messages:
            for part in message.parts:
                if (
                    part.type is MessagePartType.TOOL_CALL
                    and part.tool_call is not None
                    and part.tool_call.call_id == call_id
                ):
                    return part
        return None

    def _find_tool_result_part(self, session: Session, call_id: str) -> Optional[MessagePart]:
        for message in session.messages:
            for part in message.parts:
                if (
                    part.type is MessagePartType.TOOL_RESULT
                    and part.tool_result is not None
                    and part.tool_result.call_id == call_id
                ):
                    return part
        return None

    def _rebind_session(self, session: Session) -> None:
        for message in session.messages:
            message.session_id = session.session_id
            for part in message.parts:
                part.session_id = session.session_id
                part.message_id = message.message_id
