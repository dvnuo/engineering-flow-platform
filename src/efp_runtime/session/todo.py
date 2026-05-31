"""Session-level todo state for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, cast


TodoItem = dict[str, str]
TodosBySession = MutableMapping[str, list[TodoItem]]


class SessionTodoStore:
    """In-memory todo state keyed by EFP runtime session id."""

    def __init__(self, todos_by_session: TodosBySession | None = None) -> None:
        self.todos_by_session: TodosBySession = (
            todos_by_session if todos_by_session is not None else {}
        )
        self._lock = RLock()

    def get(self, session_id: str | None) -> list[TodoItem]:
        with self._lock:
            return _copy_todos(self.todos_by_session.get(_session_key(session_id), []))

    def set(
        self,
        session_id: str | None,
        todos: Iterable[Mapping[str, Any]],
    ) -> list[TodoItem]:
        stored = [_copy_todo(todo) for todo in todos]
        with self._lock:
            self.todos_by_session[_session_key(session_id)] = stored
            return _copy_todos(stored)

    def clear(self, session_id: str | None) -> None:
        with self._lock:
            self.todos_by_session.pop(_session_key(session_id), None)

    def snapshot(self) -> dict[str, list[TodoItem]]:
        with self._lock:
            return {
                session_id: _copy_todos(todos)
                for session_id, todos in self.todos_by_session.items()
            }


class FileSessionTodoStore(SessionTodoStore):
    """File-backed todo state keyed by EFP runtime session id."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.todos_by_session: TodosBySession = {}
        self._lock = RLock()

    def get(self, session_id: str | None) -> list[TodoItem]:
        key = _session_key(session_id)
        with self._lock:
            todos = self._read_locked(key)
            self.todos_by_session[key] = todos
            return _copy_todos(todos)

    def set(
        self,
        session_id: str | None,
        todos: Iterable[Mapping[str, Any]],
    ) -> list[TodoItem]:
        key = _session_key(session_id)
        stored = [_copy_todo(todo) for todo in todos]
        with self._lock:
            self._write_locked(key, stored)
            self.todos_by_session[key] = stored
            return _copy_todos(stored)

    def clear(self, session_id: str | None) -> None:
        key = _session_key(session_id)
        with self._lock:
            path = self._todo_path(key)
            if path.exists():
                path.unlink()
            self.todos_by_session.pop(key, None)

    def snapshot(self) -> dict[str, list[TodoItem]]:
        with self._lock:
            snapshot: dict[str, list[TodoItem]] = {}
            for path in sorted(self.root.glob("*.json")):
                key = path.stem
                todos = self._read_file_locked(path)
                self.todos_by_session[key] = todos
                snapshot[key] = _copy_todos(todos)
            return snapshot

    def _read_locked(self, session_id: str) -> list[TodoItem]:
        path = self._todo_path(session_id)
        if not path.exists():
            return []
        return self._read_file_locked(path)

    def _read_file_locked(self, path: Path) -> list[TodoItem]:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, Mapping):
            payload = payload.get("todos", [])
        if not isinstance(payload, list):
            raise ValueError(f"todo file must contain a list: {path.name}")
        return [_copy_todo(todo) for todo in payload]

    def _write_locked(self, session_id: str, todos: list[TodoItem]) -> None:
        path = self._todo_path(session_id)
        text = json.dumps(_copy_todos(todos), indent=2, sort_keys=True, ensure_ascii=False)
        tmp_name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self.root),
                prefix=f".{session_id}.",
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

    def _todo_path(self, session_id: str) -> Path:
        _validate_session_id(session_id)
        path = (self.root / f"{session_id}.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"invalid session_id: {session_id}") from exc
        return path


def _session_key(session_id: str | None) -> str:
    return session_id or "default"


def _copy_todos(todos: Iterable[Mapping[str, Any]]) -> list[TodoItem]:
    return [_copy_todo(todo) for todo in todos]


def _copy_todo(todo: Mapping[str, Any]) -> TodoItem:
    return {
        "content": cast(str, todo["content"]),
        "status": cast(str, todo["status"]),
        "priority": cast(str, todo.get("priority", "medium")),
    }


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str):
        raise TypeError("session_id must be a string")
    if not session_id or session_id in {".", ".."} or session_id.startswith("."):
        raise ValueError(f"invalid session_id: {session_id}")
    if "\x00" in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(f"invalid session_id: {session_id}")


__all__ = ["FileSessionTodoStore", "SessionTodoStore", "TodoItem", "TodosBySession"]
