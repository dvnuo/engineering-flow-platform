"""File-backed persistent session store for EFP runtime."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

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
from .todo import FileSessionTodoStore


# Cache sizing. These bound the extra memory the store may hold on top of what
# a request already needs; both are intentionally small so the cache can never
# become the leak it is meant to prevent.
_DEFAULT_PARSE_CACHE_MAX = 24
_DEFAULT_PARSE_CACHE_MAX_BYTES = 8 * 1024 * 1024  # do not retain giant sessions
_DEFAULT_SUMMARY_CACHE_MAX = 4096
_SUMMARY_PREVIEW_CHARS = 256
_SUMMARY_SCAN_CAP = 320  # stop deriving preview text once we have enough


@dataclass(frozen=True)
class SessionSummary:
    """Lightweight session header used by list endpoints.

    Deriving these instead of loading + deepcopying full sessions is what keeps
    ``GET /api/sessions`` from re-parsing every session file (with all inlined
    tool output) on every poll.
    """

    session_id: str
    title: Optional[str]
    custom_name: Optional[str]
    created_at: str
    updated_at: str
    message_count: int
    user_message_count: int
    first_user_preview: str
    last_preview: str


# malloc_trim(0) hands freed arenas back to the OS after a large parse burst.
# CPython's allocator otherwise keeps them mapped, which is exactly why RSS
# stayed high and did not reclaim. Best-effort: a no-op off glibc.
_MALLOC_TRIM: Any = None


def _release_allocator_memory() -> None:
    global _MALLOC_TRIM
    if _MALLOC_TRIM is False:
        return
    try:
        if _MALLOC_TRIM is None:
            import ctypes
            import ctypes.util

            lib_name = ctypes.util.find_library("c") or "libc.so.6"
            libc = ctypes.CDLL(lib_name)
            _MALLOC_TRIM = getattr(libc, "malloc_trim", False) or False
        if _MALLOC_TRIM:
            _MALLOC_TRIM(0)
    except Exception:
        _MALLOC_TRIM = False


def _summary_preview_text(message: Message) -> str:
    """Preview string for a message, mirroring gateway legacy ``content``.

    Kept byte-for-byte consistent with ``_message_content`` in gateway_facade
    for the leading characters, but stops early so a multi-MB tool result is
    never fully materialised just to render a 50-char preview.
    """
    chunks: List[str] = []
    total = 0
    for part in message.parts:
        piece: Optional[str] = None
        if part.type is MessagePartType.TEXT and part.text:
            piece = part.text
        elif part.type is MessagePartType.REASONING and part.reasoning and not chunks:
            piece = part.reasoning
        elif part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
            piece = part.tool_result.content
        elif part.type is MessagePartType.COMPACTION and part.compaction is not None:
            piece = part.compaction.summary
        elif part.type is MessagePartType.ERROR and part.text:
            piece = part.text
        if piece is None:
            continue
        # Slice each piece so a multi-MB tool result is never fully copied for a
        # preview. The kept prefix (>= the final 256-char cut) is unchanged.
        chunks.append(piece[:_SUMMARY_SCAN_CAP])
        total += min(len(piece), _SUMMARY_SCAN_CAP)
        if total >= _SUMMARY_SCAN_CAP:
            break
    return "\n".join(chunk for chunk in chunks if chunk is not None)


def _truncate_preview(value: str, limit: int = _SUMMARY_PREVIEW_CHARS) -> str:
    return value if len(value) <= limit else value[:limit]


def build_session_summary(session: Session) -> SessionSummary:
    user_count = 0
    first_user_preview = ""
    for message in session.messages:
        if message.role is MessageRole.USER:
            user_count += 1
            if not first_user_preview:
                first_user_preview = _truncate_preview(_summary_preview_text(message))
    last_preview = ""
    for message in reversed(session.messages):
        if message.role in (MessageRole.USER, MessageRole.ASSISTANT):
            last_preview = _truncate_preview(_summary_preview_text(message))
            break
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    custom_name = metadata.get("custom_session_name")
    custom_name = custom_name.strip() if isinstance(custom_name, str) and custom_name.strip() else None
    return SessionSummary(
        session_id=session.session_id,
        title=session.title,
        custom_name=custom_name,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=len(session.messages),
        user_message_count=user_count,
        first_user_preview=first_user_preview,
        last_preview=last_preview,
    )


class FileSessionStore:
    """Deterministic JSON file store for EFP runtime sessions."""

    def __init__(self, root: Union[str, Path]) -> None:
        self.root = Path(root).expanduser().resolve()
        sessions_dir = self.root / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = sessions_dir.resolve()
        checkpoints_dir = self.root / "checkpoints"
        checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir = checkpoints_dir.resolve()
        todos_dir = self.root / "todos"
        todos_dir.mkdir(parents=True, exist_ok=True)
        self.todos_dir = todos_dir.resolve()
        self._todo_store = FileSessionTodoStore(self.todos_dir)
        self._lock = RLock()
        # mtime+size validated caches. Reads that hit an unchanged file skip the
        # json.load + session_from_dict cost entirely; the summary cache lets the
        # list endpoint answer without touching full session bodies at all.
        self._parse_cache: "OrderedDict[str, Tuple[int, int, Session]]" = OrderedDict()
        self._summary_cache: "OrderedDict[str, Tuple[int, int, SessionSummary]]" = OrderedDict()
        self._parse_cache_max = _DEFAULT_PARSE_CACHE_MAX
        self._parse_cache_max_bytes = _DEFAULT_PARSE_CACHE_MAX_BYTES
        self._summary_cache_max = _DEFAULT_SUMMARY_CACHE_MAX

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
            session = self._read_session_locked_mutable(session_id)
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
            session = self._read_session_locked_mutable(session_id)
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
            session = self._read_session_locked_mutable(session_id)
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
            session = self._read_session_locked_mutable(session_id)
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

    def todo_store(self) -> FileSessionTodoStore:
        return self._todo_store

    def list_sessions(self) -> List[Session]:
        with self._lock:
            sessions = []
            for path in sorted(self.sessions_dir.glob("*.json")):
                # Full-list scans must not evict the hot single-session entries
                # from the small parse LRU, so they read without storing.
                sessions.append(self._read_session_file_locked(path, cache_store=False))
            return deepcopy(sessions)

    def list_session_summaries(self) -> List[SessionSummary]:
        """Return lightweight headers for every session without loading bodies.

        Unchanged files are answered straight from the summary cache (a stat
        call, no parse); only a file whose mtime/size changed is re-read. This
        is the replacement for ``list_sessions()`` on the list endpoint, which
        otherwise parsed and deepcopied every session (including inlined tool
        output) on every poll.
        """
        with self._lock:
            summaries: List[SessionSummary] = []
            parsed_any = False
            for path in sorted(self.sessions_dir.glob("*.json")):
                key = str(path)
                try:
                    stat = path.stat()
                except OSError:
                    continue
                cached = self._summary_cache.get(key)
                if (
                    cached is not None
                    and cached[0] == stat.st_mtime_ns
                    and cached[1] == stat.st_size
                ):
                    self._summary_cache.move_to_end(key)
                    summaries.append(cached[2])
                    continue
                try:
                    session = self._read_session_file_locked(path, cache_store=False)
                except Exception:
                    # A corrupt or half-written file must not break listing.
                    continue
                summary = build_session_summary(session)
                self._summary_cache_put(key, stat.st_mtime_ns, stat.st_size, summary)
                summaries.append(summary)
                parsed_any = True
            if parsed_any:
                _release_allocator_memory()
            return summaries

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            path = self._session_path(session_id)
            if not path.exists():
                self._evict_caches(session_id)
                return False
            path.unlink()
            self._evict_caches(session_id)
            self._todo_store.clear(session_id)
            _release_allocator_memory()
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

    def _read_session_file_locked(self, path: Path, *, cache_store: bool = True) -> Session:
        # The returned Session may be a shared cache object. Every public read
        # method deepcopies before returning it, and mutators go through
        # ``_read_session_locked_mutable`` (which deepcopies), so callers never
        # observe or corrupt a cached instance.
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            stat = None
        if stat is not None:
            cached = self._parse_cache.get(key)
            if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                self._parse_cache.move_to_end(key)
                return cached[2]
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        session = session_from_dict(payload)
        expected_path = self._session_path(session.session_id)
        if expected_path != path:
            raise ValueError(
                f"session file/name mismatch: expected {expected_path.name}, got {path.name}"
            )
        if cache_store and stat is not None:
            self._parse_cache_put(key, stat.st_mtime_ns, stat.st_size, session)
        return session

    def _read_session_locked_mutable(self, session_id: str) -> Session:
        """Private, mutable copy for read-modify-write callers."""
        return deepcopy(self._read_session_locked(session_id))

    def _parse_cache_put(self, key: str, mtime_ns: int, size: int, session: Session) -> None:
        if size > self._parse_cache_max_bytes:
            # Never retain oversized sessions; the summary cache still covers
            # the list path, and re-parsing one giant session on demand is far
            # cheaper than pinning it in memory.
            self._parse_cache.pop(key, None)
            return
        self._parse_cache[key] = (mtime_ns, size, session)
        self._parse_cache.move_to_end(key)
        while len(self._parse_cache) > self._parse_cache_max:
            self._parse_cache.popitem(last=False)

    def _summary_cache_put(self, key: str, mtime_ns: int, size: int, summary: SessionSummary) -> None:
        self._summary_cache[key] = (mtime_ns, size, summary)
        self._summary_cache.move_to_end(key)
        while len(self._summary_cache) > self._summary_cache_max:
            self._summary_cache.popitem(last=False)

    def _refresh_caches_after_write(self, path: Path, session: Session) -> None:
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            self._parse_cache.pop(key, None)
            self._summary_cache.pop(key, None)
            return
        self._parse_cache_put(key, stat.st_mtime_ns, stat.st_size, session)
        self._summary_cache_put(
            key, stat.st_mtime_ns, stat.st_size, build_session_summary(session)
        )

    def _evict_caches(self, session_id: str) -> None:
        key = str(self._session_path(session_id))
        self._parse_cache.pop(key, None)
        self._summary_cache.pop(key, None)

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
            # Serve the just-written state from cache instead of re-parsing it
            # on the next read. Callers do not mutate ``session`` after the
            # write returns, so caching the reference is safe.
            self._refresh_caches_after_write(path, session)
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
