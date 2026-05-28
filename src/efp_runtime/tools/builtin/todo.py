"""Session-local todo planning tool for EFP Runtime v2."""

from __future__ import annotations

import json
from typing import Any

from ...events import RuntimeEvent
from ...permissions import ALLOW, PermissionMetadata
from ...types import ToolResult
from ..definition import ToolContext, ToolDef


TODO_STATUSES = ("pending", "in_progress", "completed", "cancelled")
TODO_PRIORITIES = ("high", "medium", "low")
TodoStore = dict[str, list[dict[str, str]]]


def create_todo_write_tool(
    *,
    todos_by_session: TodoStore | None = None,
) -> ToolDef:
    return _create_todo_tool(
        tool_id="todo_write",
        todos_by_session=todos_by_session,
    )


def create_todowrite_tool(
    *,
    todos_by_session: TodoStore | None = None,
) -> ToolDef:
    return _create_todo_tool(
        tool_id="todowrite",
        todos_by_session=todos_by_session,
    )


def _create_todo_tool(
    *,
    tool_id: str,
    todos_by_session: TodoStore | None,
) -> ToolDef:
    store = todos_by_session if todos_by_session is not None else {}

    async def execute(args: dict[str, Any], context: ToolContext) -> ToolResult:
        normalized = [
            {
                "content": todo["content"],
                "status": todo["status"],
                "priority": todo.get("priority", "medium"),
            }
            for todo in args["todos"]
        ]
        session_key = context.session_id or "default"
        store[session_key] = normalized
        output = {"todos": normalized}
        counts = _todo_counts(normalized)
        metadata = {
            "todos": normalized,
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
                        "todos": normalized,
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
                        "required": ["content", "status"],
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
        runtime_metadata={"todos_by_session": store},
    )


def _todo_counts(todos: list[dict[str, str]]) -> dict[str, int]:
    completed_count = sum(1 for todo in todos if todo["status"] == "completed")
    cancelled_count = sum(1 for todo in todos if todo["status"] == "cancelled")
    return {
        "todo_count": len(todos),
        "active_todo_count": len(todos) - completed_count - cancelled_count,
        "completed_todo_count": completed_count,
        "cancelled_todo_count": cancelled_count,
    }
