"""Session-level todo state for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from threading import RLock
from typing import Any, cast


TodoItem = dict[str, str]
TodosBySession = MutableMapping[str, list[TodoItem]]


class SessionTodoStore:
    """In-memory todo state keyed by Runtime v2 session id."""

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


__all__ = ["SessionTodoStore", "TodoItem", "TodosBySession"]
