from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from efp_runtime.agents import (
    BackgroundTaskManager,
    BackgroundTaskRecord,
    create_agent_task_tools,
)
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
    format_background_task_notification,
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
async def test_pending_injections_return_final_records_once_without_draining():
    release = asyncio.Event()
    manager = BackgroundTaskManager()

    async def runner(request: TaskToolRequest) -> str:
        if request.task_id == "task-running":
            await release.wait()
        return f"{request.task_id} result"

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

    await _start_background_task(runtime, "task-inject", "session-inject")
    await _start_background_task(runtime, "task-other", "session-other")
    await _start_background_task(runtime, "task-running", "session-inject")
    await _wait_for_task_state(runtime, "task-inject", "completed")
    await _wait_for_task_state(runtime, "task-other", "completed")

    first = manager.pending_injections(session_id="session-inject")
    second = manager.pending_injections(session_id="session-inject")
    drained = manager.drain_completed(session_id="session-inject")

    assert [record.task_id for record in first] == ["task-inject"]
    assert second == []
    assert [record.task_id for record in drained] == ["task-inject"]

    release.set()
    await _wait_for_task_state(runtime, "task-running", "completed")


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


@pytest.mark.asyncio
async def test_agent_runtime_resume_injects_completed_background_task_message():
    manager = BackgroundTaskManager()
    provider = ScriptedLLMProvider([{"content": "resume observed"}])

    async def runner(request: TaskToolRequest) -> str:
        return "resume background result"

    runtime = _agent_runtime_with_background_tool(
        provider=provider,
        manager=manager,
        runner=runner,
    )
    runtime.store.create_session(session_id="session-resume-inject")

    await _start_background_task(
        runtime.tool_runtime,
        "task-resume-inject",
        "session-resume-inject",
    )
    await _wait_for_task_state(
        runtime.tool_runtime,
        "task-resume-inject",
        "completed",
    )

    result = await runtime.resume("session-resume-inject")

    request_messages = provider.requests[0].provider_request.messages
    user_messages = [
        message for message in request_messages if message.role == "user"
    ]
    injected_text = user_messages[0].text
    history = runtime.store.read_history("session-resume-inject")

    assert result.status == LoopStatus.COMPLETED
    assert len(user_messages) == 1
    assert '<task id="task-resume-inject" state="completed">' in injected_text
    assert (
        "<summary>Background task completed: Background task</summary>"
        in injected_text
    )
    assert "<task_result>\nresume background result\n</task_result>" in injected_text
    assert history[0].metadata == {
        "source": "background_task.injected",
        "synthetic": True,
        "background_task_ids": ["task-resume-inject"],
    }
    drained = runtime.drain_background_tasks("session-resume-inject")
    assert [record["task_id"] for record in drained] == ["task-resume-inject"]


@pytest.mark.asyncio
async def test_agent_runtime_run_injects_background_task_before_new_user_message():
    manager = BackgroundTaskManager()
    provider = ScriptedLLMProvider([{"content": "run observed"}])

    async def runner(request: TaskToolRequest) -> str:
        return "run background result"

    runtime = _agent_runtime_with_background_tool(
        provider=provider,
        manager=manager,
        runner=runner,
    )
    runtime.store.create_session(session_id="session-run-inject")

    await _start_background_task(
        runtime.tool_runtime,
        "task-run-inject",
        "session-run-inject",
    )
    await _wait_for_task_state(
        runtime.tool_runtime,
        "task-run-inject",
        "completed",
    )

    result = await runtime.run("Continue after task.", session_id="session-run-inject")

    user_messages = [
        message
        for message in provider.requests[0].provider_request.messages
        if message.role == "user"
    ]

    assert result.status == LoopStatus.COMPLETED
    assert len(user_messages) == 2
    assert '<task id="task-run-inject" state="completed">' in user_messages[0].text
    assert "<task_result>\nrun background result\n</task_result>" in user_messages[0].text
    assert user_messages[1].text == "Continue after task."


@pytest.mark.asyncio
async def test_background_task_result_injection_can_be_disabled_without_draining():
    manager = BackgroundTaskManager()
    provider = ScriptedLLMProvider([{"content": "disabled observed"}])

    async def runner(request: TaskToolRequest) -> str:
        return "disabled background result"

    runtime = _agent_runtime_with_background_tool(
        provider=provider,
        manager=manager,
        runner=runner,
        config=RuntimeConfig(
            max_iterations=1,
            inject_background_task_results=False,
        ),
    )
    runtime.store.create_session(session_id="session-inject-disabled")

    await _start_background_task(
        runtime.tool_runtime,
        "task-inject-disabled",
        "session-inject-disabled",
    )
    await _wait_for_task_state(
        runtime.tool_runtime,
        "task-inject-disabled",
        "completed",
    )

    result = await runtime.run("No automatic injection.", "session-inject-disabled")

    request_text = "\n".join(
        message.text for message in provider.requests[0].provider_request.messages
    )
    drained = runtime.drain_background_tasks("session-inject-disabled")

    assert result.status == LoopStatus.COMPLETED
    assert "task-inject-disabled" not in request_text
    assert [record["task_id"] for record in drained] == ["task-inject-disabled"]
    assert drained[0]["text"] == "disabled background result"


def test_background_task_notification_uses_task_error_for_error_and_cancelled():
    error_record = BackgroundTaskRecord(
        task_id='task-"error"',
        description="Broken task",
        prompt="Fail.",
        subagent_type="general",
        session_id="session-format",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        state="error",
        result=TaskToolResult(
            task_id='task-"error"',
            text="boom",
            state="error",
        ),
        error="boom",
    )
    cancelled_record = BackgroundTaskRecord(
        task_id="task-cancelled",
        description="Cancel task",
        prompt="Cancel.",
        subagent_type="general",
        session_id="session-format",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:00:01Z",
        state="cancelled",
        result=TaskToolResult(
            task_id="task-cancelled",
            text="Task cancelled.",
            state="cancelled",
        ),
        error=None,
    )

    error_text = format_background_task_notification(error_record)
    cancelled_text = format_background_task_notification(cancelled_record)

    assert '<task id="task-&quot;error&quot;" state="error">' in error_text
    assert "<summary>Background task failed: Broken task</summary>" in error_text
    assert "<task_error>\nboom\n</task_error>" in error_text
    assert "<task_result>" not in error_text
    assert '<task id="task-cancelled" state="cancelled">' in cancelled_text
    assert (
        "<summary>Background task failed: Cancel task</summary>"
        in cancelled_text
    )
    assert "<task_error>\nTask cancelled.\n</task_error>" in cancelled_text
    assert "<task_result>" not in cancelled_text


def test_agent_runtime_drain_background_tasks_without_manager_returns_empty():
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        tool_registry=ToolRegistry(),
    )

    assert runtime.drain_background_tasks() == []


def _agent_runtime_with_background_tool(
    *,
    provider: ScriptedLLMProvider,
    manager: BackgroundTaskManager,
    runner,
    config: RuntimeConfig | None = None,
) -> AgentRuntime:
    return AgentRuntime(
        provider=provider,
        config=config,
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
