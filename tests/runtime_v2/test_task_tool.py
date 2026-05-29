from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
from efp_runtime.models import ToolCall
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.builtin.task import (
    BACKGROUND_UNSUPPORTED_MESSAGE,
    TaskToolRequest,
    TaskToolResult,
    create_task_tool,
)
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def test_create_task_tool_schema_and_id():
    async def runner(request: TaskToolRequest) -> str:
        return "ok"

    tool = create_task_tool(runner, tool_id="task")

    assert tool.id == "task"
    assert (
        tool.description
        == "Delegate a task to an injected Runtime v2 task runner."
    )
    assert tool.input_schema == {
        "type": "object",
        "required": ["description", "prompt", "subagent_type"],
        "properties": {
            "description": {"type": "string"},
            "prompt": {"type": "string"},
            "subagent_type": {"type": "string"},
            "task_id": {"type": "string"},
            "command": {"type": "string"},
            "background": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    assert tool.permission.category == "task"
    assert tool.permission.data["subject_arg"] == "subagent_type"


@pytest.mark.asyncio
async def test_foreground_runner_receives_full_request_and_session_id():
    captured: list[TaskToolRequest] = []

    async def runner(request: TaskToolRequest) -> TaskToolResult:
        captured.append(request)
        return TaskToolResult(
            task_id=request.task_id,
            text="child result",
            metadata={"runner": "seen"},
        )

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await runtime.execute(
        ToolCall(
            id="call-task",
            tool_id="task",
            args={
                "description": "Summarize build failure",
                "prompt": "Inspect the logs and summarize the failure.",
                "subagent_type": "debugger",
                "task_id": "task-explicit",
                "command": "inspect-ci",
                "background": False,
            },
        ),
        context=ToolContext(
            session_id="session-task",
            request_id="request-1",
            metadata={"trace_id": "trace-1"},
        ),
    )

    assert result.status == "success"
    assert result.success is True
    assert len(captured) == 1
    request = captured[0]
    assert request == TaskToolRequest(
        description="Summarize build failure",
        prompt="Inspect the logs and summarize the failure.",
        subagent_type="debugger",
        task_id="task-explicit",
        command="inspect-ci",
        background=False,
        session_id="session-task",
        metadata={
            "trace_id": "trace-1",
            "tool_call_id": "call-task",
            "tool_name": "task",
            "request_id": "request-1",
        },
    )
    assert result.metadata["task_id"] == "task-explicit"
    assert result.metadata["description"] == "Summarize build failure"
    assert result.metadata["subagent_type"] == "debugger"
    assert result.metadata["background"] is False
    assert result.metadata["task_result_metadata"] == {"runner": "seen"}
    assert result.output["task_id"] == "task-explicit"
    assert result.output["description"] == "Summarize build failure"
    assert result.output["subagent_type"] == "debugger"
    assert result.output["background"] is False


@pytest.mark.asyncio
async def test_foreground_output_uses_opencode_like_task_tags():
    async def runner(request: TaskToolRequest) -> str:
        return "The subtask is complete."

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await runtime.execute(
        ToolCall(
            id="call-task",
            tool_id="task",
            args={
                "description": "Do a subtask",
                "prompt": "Return a result.",
                "subagent_type": "general",
                "task_id": "task-tags",
            },
        ),
        context=ToolContext(session_id="session-tags"),
    )

    assert result.status == "success"
    assert result.content == "\n".join(
        [
            '<task id="task-tags" state="completed">',
            "<task_result>",
            "The subtask is complete.",
            "</task_result>",
            "</task>",
        ]
    )


@pytest.mark.asyncio
async def test_background_true_is_rejected_by_default_and_runner_is_not_called():
    called = False

    async def runner(request: TaskToolRequest) -> str:
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await runtime.execute(
        ToolCall(
            id="call-task",
            tool_id="task",
            args={
                "description": "Run later",
                "prompt": "Start a background task.",
                "subagent_type": "general",
                "task_id": "task-background",
                "background": True,
            },
        ),
        context=ToolContext(session_id="session-background"),
    )

    assert result.status == "error"
    assert result.success is False
    assert called is False
    assert BACKGROUND_UNSUPPORTED_MESSAGE in result.content
    assert "<task_error>" in result.content
    assert result.metadata["task_id"] == "task-background"
    assert result.metadata["background"] is True


@pytest.mark.asyncio
async def test_runner_exception_returns_tool_runtime_error_status():
    async def runner(request: TaskToolRequest) -> str:
        raise RuntimeError("subtask failed")

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await runtime.execute(
        ToolCall(
            id="call-task",
            tool_id="task",
            args={
                "description": "Fail",
                "prompt": "Raise.",
                "subagent_type": "general",
                "task_id": "task-error",
            },
        ),
        context=ToolContext(session_id="session-error"),
    )

    assert result.status == "error"
    assert result.success is False
    assert result.error == "subtask failed"
    assert "<task_error>" in result.content
    assert "subtask failed" in result.content


@pytest.mark.asyncio
async def test_runner_error_result_returns_tool_runtime_error_status():
    async def runner(request: TaskToolRequest) -> TaskToolResult:
        return TaskToolResult(task_id=request.task_id, text="runner said no", state="error")

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await runtime.execute(
        ToolCall(
            id="call-task",
            tool_id="task",
            args={
                "description": "Structured failure",
                "prompt": "Return error.",
                "subagent_type": "general",
                "task_id": "task-structured-error",
            },
        ),
    )

    assert result.status == "error"
    assert result.success is False
    assert result.error == "runner said no"
    assert "<task_error>" in result.content


@pytest.mark.asyncio
async def test_core_registry_registers_task_by_default_and_accepts_runner(tmp_path: Path):
    async def runner(request: TaskToolRequest) -> str:
        return "ok"

    default_registry = create_core_tool_registry(tmp_path)
    task_registry = create_core_tool_registry(tmp_path, task_runner=runner)

    assert "task" in default_registry.ids()
    assert "task" in task_registry.ids()


@pytest.mark.asyncio
async def test_loop_integration_routes_task_output_back_to_provider():
    runner_requests: list[TaskToolRequest] = []

    async def task_runner(request: TaskToolRequest) -> str:
        runner_requests.append(request)
        return "child analysis"

    class TaskAwareProvider:
        def __init__(self) -> None:
            self.requests: list[RuntimeRequest] = []

        async def invoke(self, request: RuntimeRequest) -> dict[str, Any]:
            self.requests.append(request)
            if request.iteration == 1:
                assert [tool.id for tool in request.tools] == ["task"]
                return {
                    "tool_calls": [
                        {
                            "id": "call-task-loop",
                            "type": "function",
                            "function": {
                                "name": "task",
                                "arguments": json.dumps(
                                    {
                                        "description": "Analyze logs",
                                        "prompt": "Find the failing step.",
                                        "subagent_type": "debugger",
                                        "task_id": "task-loop",
                                    },
                                    sort_keys=True,
                                ),
                            },
                        }
                    ]
                }

            assert request.iteration == 2
            assert request.messages[-1].role is MessageRole.TOOL
            tool_part = request.messages[-1].parts[0]
            assert tool_part.type is MessagePartType.TOOL_RESULT
            assert tool_part.tool_result is not None
            assert tool_part.tool_result.call_id == "call-task-loop"
            assert tool_part.tool_result.content == "\n".join(
                [
                    '<task id="task-loop" state="completed">',
                    "<task_result>",
                    "child analysis",
                    "</task_result>",
                    "</task>",
                ]
            )
            return {"content": "Final answer from task output."}

    provider = TaskAwareProvider()
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([create_task_tool(task_runner)])),
        max_iterations=3,
    )

    result = await runner.run(session_id="session-loop-task", user_text="Delegate.")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 2
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].text == "Final answer from task output."
    assert len(runner_requests) == 1
    assert runner_requests[0].session_id == "session-loop-task"
    assert len(provider.requests) == 2

    history = store.read_history(result.session_id)
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]


def test_task_tool_import_standalone_without_legacy_runtime():
    code = """
import importlib
import json
import sys

importlib.import_module("efp_runtime.tools.builtin.task")
legacy_modules = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"legacy_loaded": []}
