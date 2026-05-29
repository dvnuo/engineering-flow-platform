"""Session-local todo planning tool for EFP Runtime v2."""

from __future__ import annotations

import json
from typing import Any

from ...events import RuntimeEvent
from ...permissions import ALLOW, PermissionMetadata
from ...session.todo import SessionTodoStore
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")
TODO_PRIORITIES = ("high", "medium", "low")
TodoStore = dict[str, list[dict[str, str]]]


def create_todowrite_tool(
    *,
    todo_store: SessionTodoStore | None = None,
    todos_by_session: TodoStore | None = None,
) -> ToolDef:
    return _create_todo_tool(
        tool_id="todowrite",
        todo_store=todo_store,
        todos_by_session=todos_by_session,
    )


def _create_todo_tool(
    *,
    tool_id: str,
    todo_store: SessionTodoStore | None,
    todos_by_session: TodoStore | None,
) -> ToolDef:
    store = _resolve_todo_store(
        todo_store=todo_store,
        todos_by_session=todos_by_session,
    )

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        normalized = [
            {
                "content": todo["content"],
                "status": todo["status"],
                "priority": todo["priority"],
            }
            for todo in args["todos"]
        ]
        todos = store.set(context.session_id, normalized)
        counts = _todo_counts(todos)
        output = {
            "todos": todos,
            **counts,
        }
        metadata = {
            "todos": todos,
            **counts,
        }
        return ToolResult(
            call_id=context.tool_call_id or "",
            tool_name=tool_id,
            status="success",
            success=True,
            content=json.dumps(output, sort_keys=True),
            output=output,
            metadata=metadata,
            events=[
                RuntimeEvent(
                    type="todo.updated",
                    session_id=context.session_id,
                    payload={
                        "tool_id": tool_id,
                        "tool_call_id": context.tool_call_id,
                        "todos": todos,
                        **counts,
                    },
                )
            ],
        )

    return ToolDef(
        id=tool_id,
        description="Store a session-local todo list for model-visible planning.",
        input_schema={
            "type": "object",
            "required": ["todos"],
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["content", "status", "priority"],
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": list(TODO_STATUSES)},
                            "priority": {
                                "type": "string",
                                "enum": list(TODO_PRIORITIES),
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=PermissionMetadata(
            action=ALLOW,
            category="planning",
            resource="session",
            risk="low",
        ),
        runtime_metadata={
            "todo_store": store,
            "todos_by_session": store.todos_by_session,
        },
    )


def _resolve_todo_store(
    *,
    todo_store: SessionTodoStore | None,
    todos_by_session: TodoStore | None,
) -> SessionTodoStore:
    if todo_store is not None:
        return todo_store
    return SessionTodoStore(todos_by_session)


def _todo_counts(todos: list[dict[str, str]]) -> dict[str, int]:
    completed_count = sum(1 for todo in todos if todo["status"] == "completed")
    cancelled_count = sum(1 for todo in todos if todo["status"] == "cancelled")
    return {
        "todo_count": len(todos),
        "active_todo_count": len(todos) - completed_count - cancelled_count,
        "completed_todo_count": completed_count,
        "cancelled_todo_count": cancelled_count,
    }
