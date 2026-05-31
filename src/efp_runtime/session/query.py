"""Read-only query helpers for EFP runtime session history."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any, Callable, TypeVar

from .models import Message, MessagePartType, Session


T = TypeVar("T")


def query_sessions(
    sessions: Iterable[Session],
    *,
    limit: int | None = None,
    order: str = "desc",
    cursor: Mapping[str, Any] | None = None,
    search: str | None = None,
    roots: bool = False,
    path: str | None = None,
    workspace_id: str | None = None,
    parent_session_id: str | None = None,
    start: str | None = None,
) -> list[Session]:
    """Filter, sort, and page sessions without mutating caller-owned objects."""

    _validate_order(order)
    _validate_limit(limit)
    items = list(sessions)
    filtered = [
        session
        for session in items
        if _session_matches(
            session,
            search=search,
            roots=roots,
            path=path,
            workspace_id=workspace_id,
            parent_session_id=parent_session_id,
            start=start,
        )
    ]
    ordered = _sort_items(
        filtered,
        key=lambda session: (session.updated_at, session.session_id),
        order=order,
    )
    page = _apply_cursor(
        ordered,
        cursor=cursor,
        order=order,
        limit=limit,
        id_names=("id", "session_id"),
        object_id=lambda session: session.session_id,
        object_time=lambda session: session.updated_at,
        cursor_lookup=items,
    )
    return deepcopy(page)


def query_messages(
    messages: Iterable[Message],
    *,
    limit: int | None = None,
    order: str = "asc",
    cursor: Mapping[str, Any] | None = None,
) -> list[Message]:
    """Sort and page messages without mutating caller-owned objects."""

    _validate_order(order)
    _validate_limit(limit)
    ordered = _sort_items(
        list(messages),
        key=lambda message: (message.created_at, message.message_id),
        order=order,
    )
    page = _apply_cursor(
        ordered,
        cursor=cursor,
        order=order,
        limit=limit,
        id_names=("id", "message_id"),
        object_id=lambda message: message.message_id,
        object_time=lambda message: message.created_at,
        cursor_lookup=ordered,
    )
    return deepcopy(page)


def session_context_messages(messages: Iterable[Message]) -> list[Message]:
    """Return the effective ascending context from the latest compaction message."""

    ordered = _sort_items(
        list(messages),
        key=lambda message: (message.created_at, message.message_id),
        order="asc",
    )
    start_index = 0
    for index, message in enumerate(ordered):
        if any(part.type is MessagePartType.COMPACTION for part in message.parts):
            start_index = index
    return deepcopy(ordered[start_index:])


def _session_matches(
    session: Session,
    *,
    search: str | None,
    roots: bool,
    path: str | None,
    workspace_id: str | None,
    parent_session_id: str | None,
    start: str | None,
) -> bool:
    metadata = session.metadata
    if search is not None:
        if session.title is None or search.lower() not in session.title.lower():
            return False
    if roots and metadata.get("parent_session_id"):
        return False
    if parent_session_id is not None and metadata.get("parent_session_id") != parent_session_id:
        return False
    if path is not None:
        session_path = metadata.get("path")
        if session_path != path and not (
            isinstance(session_path, str) and session_path.startswith(path + "/")
        ):
            return False
    if workspace_id is not None and metadata.get("workspace_id") != workspace_id:
        return False
    if start is not None and session.updated_at < start:
        return False
    return True


def _sort_items(
    items: list[T],
    *,
    key: Callable[[T], tuple[str, str]],
    order: str,
) -> list[T]:
    return sorted(items, key=key, reverse=order == "desc")


def _apply_cursor(
    ordered: list[T],
    *,
    cursor: Mapping[str, Any] | None,
    order: str,
    limit: int | None,
    id_names: tuple[str, str],
    object_id: Callable[[T], str],
    object_time: Callable[[T], str],
    cursor_lookup: Iterable[T],
) -> list[T]:
    if limit == 0:
        return []

    page = ordered
    if cursor is not None:
        direction = str(cursor.get("direction", "next"))
        if direction not in {"next", "previous"}:
            raise ValueError("cursor direction must be 'next' or 'previous'")
        cursor_key = _cursor_key(
            cursor_lookup,
            cursor=cursor,
            id_names=id_names,
            object_id=object_id,
            object_time=object_time,
        )

        def is_after(item: T) -> bool:
            item_key = (object_time(item), object_id(item))
            if order == "asc":
                return item_key > cursor_key
            return item_key < cursor_key

        def is_before(item: T) -> bool:
            item_key = (object_time(item), object_id(item))
            if order == "asc":
                return item_key < cursor_key
            return item_key > cursor_key

        if direction == "next":
            page = [item for item in ordered if is_after(item)]
        else:
            previous = [item for item in ordered if is_before(item)]
            page = previous[-limit:] if limit is not None else previous

    if limit is not None:
        page = page[:limit]
    return page


def _cursor_key(
    items: Iterable[T],
    *,
    cursor: Mapping[str, Any],
    id_names: tuple[str, str],
    object_id: Callable[[T], str],
    object_time: Callable[[T], str],
) -> tuple[str, str]:
    cursor_id = _cursor_id(cursor, id_names)
    cursor_time = cursor.get("time")
    if cursor_time is None:
        for item in items:
            if object_id(item) == cursor_id:
                cursor_time = object_time(item)
                break
        else:
            raise ValueError(f"cursor object not found: {cursor_id}")
    return str(cursor_time), cursor_id


def _cursor_id(cursor: Mapping[str, Any], id_names: tuple[str, str]) -> str:
    for name in id_names:
        value = cursor.get(name)
        if value is not None:
            return str(value)
    joined = "' or '".join(id_names)
    raise ValueError(f"cursor must include '{joined}'")


def _validate_limit(limit: int | None) -> None:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")


def _validate_order(order: str) -> None:
    if order not in {"asc", "desc"}:
        raise ValueError("order must be 'asc' or 'desc'")


__all__ = ["query_messages", "query_sessions", "session_context_messages"]
