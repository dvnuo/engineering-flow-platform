from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from efp_runtime.agents import BackgroundTaskManager, create_agent_task_tools
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin.task import (
    BACKGROUND_UNSUPPORTED_MESSAGE,
    TaskToolRequest,
    TaskToolResult,
    create_task_cancel_tool,
    create_task_status_tool,
    create_task_tool,
)
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


@pytest.mark.asyncio
async def test_background_task_returns_running_without_waiting_for_runner():
    release = asyncio.Event()
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> TaskToolResult:
        await release.wait()
        return TaskToolResult(task_id=request.task_id, text="slow result")

    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                ),
                create_task_status_tool(manager),
            ]
        )
    )
    started = time.monotonic()

    result = await _start_background_task(runtime, "task-slow", "session-slow")
    duration = time.monotonic() - started

    assert result.status == "success"
    assert duration < 0.5
    assert result.output["task_id"] == "task-slow"
    assert result.output["state"] == "running"
    assert result.output["background"] is True
    assert result.metadata["background_task"] is True
    assert '<task id="task-slow" state="running">' in result.content
    assert "task_status" in result.content

    release.set()
    completed = await _wait_for_task_state(runtime, "task-slow", "completed")
    assert completed.output["text"] == "slow result"


@pytest.mark.asyncio
async def test_task_status_reports_completed_result_text_and_metadata():
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> TaskToolResult:
        return TaskToolResult(
            task_id=request.task_id,
            text="child result",
            metadata={
                "child_session_id": "child-session",
                "child_status": "completed",
            },
        )

    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                ),
                create_task_status_tool(manager),
            ]
        )
    )

    await _start_background_task(runtime, "task-status", "session-status")
    status = await _wait_for_task_state(runtime, "task-status", "completed")

    assert status.status == "success"
    assert status.output["state"] == "completed"
    assert status.output["text"] == "child result"
    assert status.output["result_metadata"]["child_session_id"] == "child-session"
    assert status.metadata["task_result_metadata"]["child_status"] == "completed"
    assert "<task_result>\nchild result\n</task_result>" in status.content


@pytest.mark.asyncio
async def test_task_status_drain_returns_completed_records_once():
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> str:
        return "drained result"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                ),
                create_task_status_tool(manager),
            ]
        )
    )

    await _start_background_task(runtime, "task-drain", "session-drain")
    await _wait_for_task_state(runtime, "task-drain", "completed")

    first = await runtime.execute(
        ToolCall(
            id="call-drain-first",
            tool_id="task_status",
            args={"session_id": "session-drain", "drain": True},
        ),
        context=ToolContext(session_id="session-drain"),
    )
    second = await runtime.execute(
        ToolCall(
            id="call-drain-second",
            tool_id="task_status",
            args={"session_id": "session-drain", "drain": True},
        ),
        context=ToolContext(session_id="session-drain"),
    )

    assert first.output["count"] == 1
    assert first.output["tasks"][0]["task_id"] == "task-drain"
    assert first.output["tasks"][0]["text"] == "drained result"
    assert second.output == {
        "tasks": [],
        "count": 0,
        "session_id": "session-drain",
        "drain": True,
    }


@pytest.mark.asyncio
async def test_task_cancel_cancels_running_background_task():
    started = asyncio.Event()
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> str:
        started.set()
        await asyncio.Event().wait()
        return "unreachable"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                ),
                create_task_status_tool(manager),
                create_task_cancel_tool(manager),
            ]
        ),
        permission_evaluator=AllowEvaluator(),
    )

    await _start_background_task(runtime, "task-cancel", "session-cancel")
    await asyncio.wait_for(started.wait(), timeout=1)

    cancel = await runtime.execute(
        ToolCall(
            id="call-cancel",
            tool_id="task_cancel",
            args={"task_id": "task-cancel"},
        ),
        context=ToolContext(session_id="session-cancel"),
    )
    status = await _wait_for_task_state(runtime, "task-cancel", "cancelled")

    assert cancel.status == "success"
    assert cancel.output["state"] == "cancelled"
    assert status.output["state"] == "cancelled"
    assert status.output["text"] == "Task cancelled."


@pytest.mark.asyncio
async def test_unknown_background_task_id_returns_tool_error():
    manager = BackgroundTaskManager()
    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_status_tool(manager),
                create_task_cancel_tool(manager),
            ]
        ),
        permission_evaluator=AllowEvaluator(),
    )

    status = await runtime.execute(
        ToolCall(
            id="call-status-missing",
            tool_id="task_status",
            args={"task_id": "task-missing"},
        )
    )
    cancel = await runtime.execute(
        ToolCall(
            id="call-cancel-missing",
            tool_id="task_cancel",
            args={"task_id": "task-missing"},
        )
    )

    assert status.status == "error"
    assert status.success is False
    assert "Unknown background task: task-missing" in status.error
    assert cancel.status == "error"
    assert cancel.success is False
    assert "Unknown background task: task-missing" in cancel.error


@pytest.mark.asyncio
async def test_foreground_task_still_waits_for_runner_when_background_false():
    release = asyncio.Event()
    completed = False
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> str:
        nonlocal completed
        await release.wait()
        completed = True
        return "foreground result"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                )
            ]
        )
    )
    pending = asyncio.create_task(
        runtime.execute(
            ToolCall(
                id="call-foreground",
                tool_id="task",
                args={
                    "description": "Run now",
                    "prompt": "Return foreground.",
                    "subagent_type": "general",
                    "task_id": "task-foreground",
                    "background": False,
                },
            ),
            context=ToolContext(session_id="session-foreground"),
        )
    )
    await asyncio.sleep(0.05)

    assert pending.done() is False
    assert completed is False

    release.set()
    result = await pending

    assert result.status == "success"
    assert result.output["state"] == "completed"
    assert result.output["text"] == "foreground result"
    assert manager.list() == []


@pytest.mark.asyncio
async def test_background_true_is_still_unsupported_when_not_allowed():
    called = False

    async def runner(request: TaskToolRequest) -> str:
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(ToolRegistry([create_task_tool(runner)]))

    result = await _start_background_task(runtime, "task-unsupported", "session")

    assert result.status == "error"
    assert called is False
    assert BACKGROUND_UNSUPPORTED_MESSAGE in result.content


@pytest.mark.asyncio
async def test_create_agent_task_tools_share_background_manager():
    provider = ScriptedLLMProvider([{"content": "subagent complete"}])
    tools = create_agent_task_tools(
        provider=provider,
        base_config=RuntimeConfig(max_iterations=1),
        allow_background=True,
    )
    runtime = ToolRuntime(ToolRegistry(tools), permission_evaluator=AllowEvaluator())

    start = await _start_background_task(runtime, "task-agent", "session-agent")
    status = await _wait_for_task_state(runtime, start.output["task_id"], "completed")

    assert [tool.id for tool in tools] == ["task", "task_status", "task_cancel"]
    assert status.output["text"] == "subagent complete"
    assert status.output["result_metadata"]["child_session_id"].endswith("task-agent")
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_plan_mode_hides_task_cancel_but_keeps_task_status_visible():
    manager = BackgroundTaskManager()
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(runtime_mode="plan", max_iterations=1),
        tool_registry=ToolRegistry(
            [
                create_task_status_tool(manager),
                create_task_cancel_tool(manager),
            ]
        ),
    )

    result = await runtime.run("Plan.", session_id="session-plan-task-tools")

    assert result.status == LoopStatus.COMPLETED
    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    assert request_tool_ids == ["task_status"]


@pytest.mark.asyncio
async def test_agent_runtime_drain_background_tasks_returns_completed_once():
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> str:
        return "facade drain result"

    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        tool_registry=ToolRegistry(
            [
                create_task_tool(
                    runner,
                    allow_background=True,
                    background_manager=manager,
                ),
                create_task_status_tool(manager),
            ]
        ),
    )

    await _start_background_task(
        runtime.tool_runtime,
        "task-facade-drain",
        "session-facade-drain",
    )
    await _wait_for_task_state(
        runtime.tool_runtime,
        "task-facade-drain",
        "completed",
    )

    first = runtime.drain_background_tasks("session-facade-drain")
    second = runtime.drain_background_tasks("session-facade-drain")

    assert len(first) == 1
    assert first[0]["task_id"] == "task-facade-drain"
    assert first[0]["text"] == "facade drain result"
    assert second == []


def test_agent_runtime_drain_background_tasks_without_manager_returns_empty():
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        tool_registry=ToolRegistry(),
    )

    assert runtime.drain_background_tasks() == []


async def _start_background_task(
    runtime: ToolRuntime,
    task_id: str,
    session_id: str,
):
    return await runtime.execute(
        ToolCall(
            id=f"call-{task_id}",
            tool_id="task",
            args={
                "description": "Background task",
                "prompt": "Run in the background.",
                "subagent_type": "general",
                "task_id": task_id,
                "background": True,
            },
        ),
        context=ToolContext(session_id=session_id),
    )


async def _wait_for_task_state(
    runtime: ToolRuntime,
    task_id: str,
    expected_state: str,
):
    for _ in range(100):
        result = await runtime.execute(
            ToolCall(
                id=f"call-status-{task_id}",
                tool_id="task_status",
                args={"task_id": task_id},
            )
        )
        if result.output["state"] == expected_state:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach state {expected_state}")
