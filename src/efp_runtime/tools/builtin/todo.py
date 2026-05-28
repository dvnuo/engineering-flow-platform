"""Session-local todo planning tool for EFP Runtime v2."""

from __future__ import annotations

from typing import Any

from ...permissions import ALLOW, PermissionMetadata
from ..definition import ToolContext, ToolDef


TODO_STATUSES = ("pending", "in_progress", "completed")
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

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        normalized = [
            {
                "content": todo["content"],
                "status": todo["status"],
            }
            for todo in args["todos"]
        ]
        session_key = context.session_id or "default"
        store[session_key] = normalized
        return {
            "todos": normalized,
        }

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
