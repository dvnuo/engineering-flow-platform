"""File-backed persistent session store for Runtime v2."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Union

from ..types import new_id
from .checkpoint import SessionCheckpoint
from .fork import fork_session_metadata
from .models import Message, MessagePart, MessagePartType, MessageRole, Session
from .protocol import ToolPair
from .serialization import (
    checkpoint_from_dict,
    checkpoint_to_dict,
    session_from_dict,
    session_to_dict,
)


class FileSessionStore:
    """Deterministic JSON file store for Runtime v2 sessions."""

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()
        sessions_dir = self.root / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = sessions_dir.resolve()
        checkpoints_dir = self.root / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir = checkpoints_dir.resolve()
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
            path = self._session_path(session.session_id)
            if path.exists():
                raise ValueError(f"session already exists: {session.session_id}")
            self._write_session_locked(session)
            return deepcopy(session)

    def get_session(self, session_id: str) -> Session:
        with self._lock:
            return deepcopy(self._read_session_locked(session_id))

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        replace_metadata: bool = False,
    ) -> Session:
        with self._lock:
            session = self._read_session_locked(session_id)
            if title is not None:
                session.title = title
            if replace_metadata:
                session.metadata = deepcopy(dict(metadata or {}))
            elif metadata is not None:
                updated_metadata = deepcopy(session.metadata)
                updated_metadata.update(deepcopy(dict(metadata)))
                session.metadata = updated_metadata
            session.touch()
            self._write_session_locked(session)
            return deepcopy(session)

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
        with self._lock:
            session = self._read_session_locked(session_id)
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
            self._write_session_locked(session)
            return deepcopy(message)

    def append_part(self, session_id: str, message_id: str, part: MessagePart) -> MessagePart:
        with self._lock:
            session = self._read_session_locked(session_id)
            message = self._require_message(session, message_id)
            stored_part = self._append_part_locked(session, message, part)
            session.touch()
            self._write_session_locked(session)
            return deepcopy(stored_part)

    def read_history(self, session_id: str) -> List[Message]:
        with self._lock:
            return deepcopy(self._read_session_locked(session_id).messages)

    def replace_history(self, session_id: str, messages: Iterable[Message]) -> Session:
        with self._lock:
            session = self._read_session_locked(session_id)
            session.messages = [deepcopy(message) for message in messages]
            self._rebind_session(session)
            session.touch()
            self._write_session_locked(session)
            return deepcopy(session)

    def tool_pairs(self, session_id: str) -> Dict[str, ToolPair]:
        with self._lock:
            session = self._read_session_locked(session_id)
            pairs: Dict[str, ToolPair] = {}
            for message in session.messages:
                for part in message.parts:
                    if part.type is MessagePartType.TOOL_CALL and part.tool_call:
                        existing = pairs.get(part.tool_call.call_id)
                        pairs[part.tool_call.call_id] = (
                            part,
                            existing[1] if existing else None,
                        )
                    elif part.type is MessagePartType.TOOL_RESULT and part.tool_result:
                        call_part = self._find_tool_call_part(session, part.tool_result.call_id)
                        if call_part:
                            pairs[part.tool_result.call_id] = (call_part, part)
            return deepcopy(pairs)

    def list_sessions(self) -> List[Session]:
        with self._lock:
            sessions = []
            for path in sorted(self.sessions_dir.glob("*.json")):
                sessions.append(self._read_session_file_locked(path))
            return deepcopy(sessions)

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            path = self._session_path(session_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def fork_session(
        self,
        session_id: str,
        *,
        message_id: Optional[str] = None,
        new_session_id: Optional[str] = None,
    ) -> Session:
        with self._lock:
            source = self._read_session_locked(session_id)
            fork_id = new_session_id or new_id("session")
            fork_path = self._session_path(fork_id)
            if fork_path.exists():
                raise ValueError(f"session already exists: {fork_id}")

            messages = deepcopy(source.messages)
            if message_id is not None:
                message_index = self._message_index(source, message_id)
                messages = messages[: message_index + 1]

            forked = Session(
                session_id=fork_id,
                title=source.title,
                messages=messages,
                metadata=self._fork_metadata(source, message_id=message_id),
            )
            self._rebind_session(forked)
            self._write_session_locked(forked)
            return deepcopy(forked)

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
            source = self._read_session_locked(session_id)
            snapshot = self._snapshot_session(source, message_id=message_id)
            resolved_checkpoint_id = checkpoint_id or new_id("checkpoint")
            path = self._checkpoint_path(source.session_id, resolved_checkpoint_id)
            if path.exists():
                raise ValueError(f"checkpoint already exists: {resolved_checkpoint_id}")

            checkpoint = SessionCheckpoint(
                checkpoint_id=resolved_checkpoint_id,
                session_id=source.session_id,
                message_id=message_id,
                message_count=len(snapshot.messages),
                label=label,
                metadata=dict(metadata or {}),
            )
            self._write_checkpoint_locked(checkpoint, snapshot)
            return deepcopy(checkpoint)

    def list_checkpoints(self, session_id: str) -> List[SessionCheckpoint]:
        with self._lock:
            self._read_session_locked(session_id)
            directory = self._checkpoint_dir(session_id)
            if not directory.exists():
                return []
            checkpoints = [
                self._read_checkpoint_file_locked(path)[0]
                for path in sorted(directory.glob("*.json"))
            ]
            checkpoints.sort(
                key=lambda checkpoint: (checkpoint.created_at, checkpoint.checkpoint_id)
            )
            return deepcopy(checkpoints)

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Session:
        with self._lock:
            path = self._checkpoint_path(session_id, checkpoint_id)
            if not path.exists():
                raise KeyError(f"unknown checkpoint: {checkpoint_id}")
            checkpoint, snapshot = self._read_checkpoint_file_locked(path)
            if checkpoint.session_id != session_id:
                raise ValueError(
                    "checkpoint session mismatch: "
                    f"expected {session_id}, got {checkpoint.session_id}"
                )

            restored = deepcopy(snapshot)
            restored.session_id = session_id
            self._rebind_session(restored)
            self._write_session_locked(restored)
            return deepcopy(restored)

    def delete_checkpoint(self, session_id: str, checkpoint_id: str) -> bool:
        with self._lock:
            self._read_session_locked(session_id)
            path = self._checkpoint_path(session_id, checkpoint_id)
            if not path.exists():
                return False
            path.unlink()
            directory = path.parent
            try:
                directory.rmdir()
            except OSError:
                pass
            return True

    def _read_session_locked(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise KeyError(f"unknown session: {session_id}")
        return self._read_session_file_locked(path)

    def _read_session_file_locked(self, path: Path) -> Session:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        session = session_from_dict(payload)
        expected_path = self._session_path(session.session_id)
        if expected_path != path:
            raise ValueError(
                f"session file/name mismatch: expected {expected_path.name}, got {path.name}"
            )
        return session

    def _write_session_locked(self, session: Session) -> None:
        path = self._session_path(session.session_id)
        payload = session_to_dict(session)
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.sessions_dir),
                prefix=f".{session.session_id}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                handle.write(text)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if tmp_name is not None:
                tmp_path = Path(tmp_name)
                if tmp_path.exists():
                    tmp_path.unlink()

    def _write_checkpoint_locked(
        self,
        checkpoint: SessionCheckpoint,
        snapshot: Session,
    ) -> None:
        path = self._checkpoint_path(checkpoint.session_id, checkpoint.checkpoint_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "checkpoint": checkpoint_to_dict(checkpoint),
            "session": session_to_dict(snapshot),
        }
        text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(path.parent),
                prefix=f".{checkpoint.checkpoint_id}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                handle.write(text)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if tmp_name is not None:
                tmp_path = Path(tmp_name)
                if tmp_path.exists():
                    tmp_path.unlink()

    def _read_checkpoint_file_locked(self, path: Path) -> tuple[SessionCheckpoint, Session]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        checkpoint = checkpoint_from_dict(payload["checkpoint"])
        snapshot = session_from_dict(payload["session"])
        expected_path = self._checkpoint_path(
            checkpoint.session_id,
            checkpoint.checkpoint_id,
        )
        if expected_path != path:
            raise ValueError(
                "checkpoint file/name mismatch: "
                f"expected {expected_path.name}, got {path.name}"
            )
        return checkpoint, snapshot

    def _session_path(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        path = (self.sessions_dir / f"{session_id}.json").resolve()
        try:
            path.relative_to(self.sessions_dir)
        except ValueError as exc:
            raise ValueError(f"invalid session_id: {session_id}") from exc
        return path

    def _checkpoint_dir(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        path = (self.checkpoints_dir / session_id).resolve()
        try:
            path.relative_to(self.checkpoints_dir)
        except ValueError as exc:
            raise ValueError(f"invalid session_id: {session_id}") from exc
        return path

    def _checkpoint_path(self, session_id: str, checkpoint_id: str) -> Path:
        self._validate_checkpoint_id(checkpoint_id)
        directory = self._checkpoint_dir(session_id)
        path = (directory / f"{checkpoint_id}.json").resolve()
        try:
            path.relative_to(directory)
        except ValueError as exc:
            raise ValueError(f"invalid checkpoint_id: {checkpoint_id}") from exc
        return path

    def _validate_session_id(self, session_id: str) -> None:
        if not isinstance(session_id, str):
            raise TypeError("session_id must be a string")
        if not session_id or session_id in {".", ".."} or session_id.startswith("."):
            raise ValueError(f"invalid session_id: {session_id}")
        if "\x00" in session_id or "/" in session_id or "\\" in session_id:
            raise ValueError(f"invalid session_id: {session_id}")

    def _validate_checkpoint_id(self, checkpoint_id: str) -> None:
        if not isinstance(checkpoint_id, str):
            raise TypeError("checkpoint_id must be a string")
        if (
            not checkpoint_id
            or checkpoint_id in {".", ".."}
            or checkpoint_id.startswith(".")
        ):
            raise ValueError(f"invalid checkpoint_id: {checkpoint_id}")
        if "\x00" in checkpoint_id or "/" in checkpoint_id or "\\" in checkpoint_id:
            raise ValueError(f"invalid checkpoint_id: {checkpoint_id}")

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

    def _require_message(self, session: Session, message_id: str) -> Message:
        for message in session.messages:
            if message.message_id == message_id:
                return message
        raise KeyError(f"unknown message: {message_id}")

    def _message_index(self, session: Session, message_id: str) -> int:
        for index, message in enumerate(session.messages):
            if message.message_id == message_id:
                return index
        raise KeyError(f"unknown message: {message_id}")

    def _snapshot_session(self, session: Session, *, message_id: Optional[str]) -> Session:
        snapshot = deepcopy(session)
        if message_id is not None:
            message_index = self._message_index(snapshot, message_id)
            snapshot.messages = snapshot.messages[: message_index + 1]
        self._rebind_session(snapshot)
        return snapshot

    def _fork_metadata(
        self,
        source: Session,
        *,
        message_id: Optional[str],
    ) -> dict:
        return fork_session_metadata(
            source.metadata,
            parent_session_id=source.session_id,
            message_id=message_id,
        )

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


__all__ = ["FileSessionStore"]
