from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.todo import SessionTodoStore
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_todowrite_updates_runtime_session_todo_state(tmp_path: Path):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _provider_tool_call(
                        "call-todo",
                        "todowrite",
                        {
                            "todos": [
                                {
                                    "content": "Inspect runtime state",
                                    "status": "completed",
                                    "priority": "medium",
                                },
                                {
                                    "content": "Run focused tests",
                                    "status": "in_progress",
                                    "priority": "high",
                                },
                            ]
                        },
                    )
                ]
            },
            {"content": "Done."},
            {"content": "Still done."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=3),
    )

    result = await runtime.run("Write todos.", session_id="session-todos")

    assert result.status == LoopStatus.COMPLETED
    assert runtime.get_todos("session-todos") == [
        {
            "content": "Inspect runtime state",
            "status": "completed",
            "priority": "medium",
        },
        {
            "content": "Run focused tests",
            "status": "in_progress",
            "priority": "high",
        },
    ]

    second_result = await runtime.run("Read todos.", session_id="session-todos")

    assert second_result.status == LoopStatus.COMPLETED
    assert runtime.get_todos("session-todos") == [
        {
            "content": "Inspect runtime state",
            "status": "completed",
            "priority": "medium",
        },
        {
            "content": "Run focused tests",
            "status": "in_progress",
            "priority": "high",
        },
    ]


def test_runtime_todo_facade_returns_copies(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(workspace_root=tmp_path),
    )
    input_todos = [
        {"content": "Draft plan", "status": "pending", "priority": "medium"}
    ]

    returned = runtime.set_todos("session-copy", input_todos)
    input_todos[0]["content"] = "mutated input"
    returned[0]["content"] = "mutated return"

    stored = runtime.get_todos("session-copy")
    assert stored == [
        {"content": "Draft plan", "status": "pending", "priority": "medium"}
    ]

    stored[0]["status"] = "completed"
    assert runtime.get_todos("session-copy") == [
        {"content": "Draft plan", "status": "pending", "priority": "medium"}
    ]

    runtime.clear_todos("session-copy")
    assert runtime.get_todos("session-copy") == []


def test_runtime_todos_are_isolated_by_session_id(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        config=RuntimeConfig(workspace_root=tmp_path),
    )

    runtime.set_todos(
        "parent-session",
        [{"content": "Parent work", "status": "pending", "priority": "high"}],
    )
    runtime.set_todos(
        "child-session",
        [{"content": "Child work", "status": "completed", "priority": "low"}],
    )

    assert runtime.get_todos("parent-session") == [
        {"content": "Parent work", "status": "pending", "priority": "high"}
    ]
    assert runtime.get_todos("child-session") == [
        {"content": "Child work", "status": "completed", "priority": "low"}
    ]

    runtime.clear_todos("parent-session")
    assert runtime.get_todos("parent-session") == []
    assert runtime.get_todos("child-session") == [
        {"content": "Child work", "status": "completed", "priority": "low"}
    ]


@pytest.mark.asyncio
async def test_todowrite_uses_session_store(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)
    runtime = ToolRuntime(registry)
    store = registry.require("todowrite").runtime_metadata["todo_store"]

    assert isinstance(store, SessionTodoStore)
    assert (
        registry.require("todowrite").runtime_metadata["todos_by_session"]
        is store.todos_by_session
    )

    await runtime.execute(
        ToolCall(
            id="call-opencode",
            tool_id="todowrite",
            args={
                "todos": [
                    {
                        "content": "From opencode",
                        "status": "completed",
                        "priority": "high",
                    }
                ]
            },
        ),
        context=ToolContext(session_id="session-shared"),
    )
    assert store.get("session-shared") == [
        {"content": "From opencode", "status": "completed", "priority": "high"}
    ]


@pytest.mark.asyncio
async def test_todowrite_event_payload_shape_is_unchanged(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-event",
            tool_id="todowrite",
            args={
                "todos": [
                    {"content": "Done", "status": "completed", "priority": "medium"},
                    {"content": "Cancelled", "status": "cancelled", "priority": "medium"},
                    {"content": "Active", "status": "in_progress", "priority": "medium"},
                ]
            },
        ),
        context=ToolContext(session_id="session-events"),
    )

    todos = [
        {"content": "Done", "status": "completed", "priority": "medium"},
        {"content": "Cancelled", "status": "cancelled", "priority": "medium"},
        {"content": "Active", "status": "in_progress", "priority": "medium"},
    ]
    expected_counts = {
        "todo_count": 3,
        "active_todo_count": 1,
        "completed_todo_count": 1,
        "cancelled_todo_count": 1,
    }
    assert result.output == {"todos": todos, **expected_counts}
    todo_event = next(event for event in result.events if event.type == "todo.updated")
    assert todo_event.session_id == "session-events"
    assert todo_event.payload == {
        "tool_id": "todowrite",
        "tool_call_id": "call-event",
        "todos": todos,
        **expected_counts,
    }


def test_custom_runtime_without_todo_tool_returns_empty_todos():
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([]),
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    assert runtime.get_todos("session-missing") == []
    runtime.clear_todos("session-missing")
    assert runtime.get_todos("session-missing") == []


def _provider_tool_call(
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
