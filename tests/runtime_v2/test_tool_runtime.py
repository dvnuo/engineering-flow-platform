import pytest

from efp_runtime.events import RuntimeEvent
from efp_runtime.models import ToolCall, ToolResult
from efp_runtime.permissions import ASK, DENY, PermissionMetadata
from efp_runtime.tools.definition import OutputPolicy, ToolContext, ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_tool_runtime_validates_executes_normalizes_and_truncates():
    async def execute(args, context):
        assert isinstance(context, ToolContext)
        return {"echo": args["text"], "session": context.session_id}

    tool = ToolDef(
        id="echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
            "additionalProperties": False,
        },
        execute=execute,
        output_policy=OutputPolicy(max_chars=20),
    )
    runtime = ToolRuntime(ToolRegistry([tool]))

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="echo", args={"text": "hello world"}),
        context=ToolContext(session_id="session-1", run_id="run-1", iteration=2),
    )

    assert result.status == "success"
    assert result.truncated is True
    assert result.output == {"echo": "hello world", "session": "session-1"}
    assert result.metadata["original_chars"] > len(result.content)
    assert isinstance(result.metadata["duration_ms"], int)
    assert result.metadata["duration_ms"] >= 0
    assert [event.type for event in result.events] == ["tool.started", "tool.completed"]
    assert result.events[0].payload["arg_keys"] == ["text"]
    assert "args" not in result.events[0].payload
    assert "arguments" not in result.events[0].payload
    assert result.events[-1].type == "tool.completed"
    assert result.events[-1].payload["tool_id"] == "echo"
    assert result.events[-1].payload["tool_call_id"] == "call-1"
    assert result.events[-1].payload["session_id"] == "session-1"
    assert result.events[-1].payload["run_id"] == "run-1"
    assert result.events[-1].payload["iteration"] == 2
    assert result.events[-1].payload["status"] == "success"
    assert result.events[-1].payload["success"] is True
    assert isinstance(result.events[-1].payload["duration_ms"], int)
    assert result.events[-1].payload["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_validation_error_happens_before_execute():
    called = False

    async def execute(args, context):
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="strict",
                    description="Strict tool",
                    input_schema={
                        "type": "object",
                        "required": ["count"],
                        "properties": {"count": {"type": "integer"}},
                        "additionalProperties": False,
                    },
                    execute=execute,
                )
            ]
        )
    )

    result = await runtime.execute(ToolCall(id="call-1", tool_id="strict", args={"count": "1"}))

    assert result.status == "validation_error"
    assert "count" in result.error
    assert called is False
    assert "tool.started" not in [event.type for event in result.events]


@pytest.mark.asyncio
async def test_permission_ask_returns_request_without_execution():
    called = False

    async def execute(args, context):
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="write",
                    description="Write a file",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                    permission=PermissionMetadata(
                        action=ASK,
                        reason="Writes require approval.",
                        category="filesystem",
                    ),
                )
            ]
        )
    )

    result = await runtime.execute(ToolCall(id="call-1", tool_id="write", args={}))

    assert result.status == "permission_requested"
    assert result.metadata["permission_request"]["request_id"].startswith("perm_")
    assert result.metadata["permission_request"]["tool_id"] == "write"
    assert result.metadata["permission_request"]["action"] == "ask"
    assert result.metadata["permission_request"]["category"] == "filesystem"
    assert result.metadata["permission_request"]["reason"] == "Writes require approval."
    assert called is False
    assert "tool.started" not in [event.type for event in result.events]


@pytest.mark.asyncio
async def test_permission_deny_returns_denied_without_execution():
    called = False

    async def execute(args, context):
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="danger",
                    description="Dangerous tool",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                    permission=PermissionMetadata(action=DENY, reason="Blocked by policy."),
                )
            ]
        )
    )

    result = await runtime.execute(ToolCall(id="call-1", tool_id="danger", args={}))

    assert result.status == "permission_denied"
    assert result.error == "Blocked by policy."
    assert called is False
    assert "tool.started" not in [event.type for event in result.events]


@pytest.mark.asyncio
async def test_execute_exception_emits_started_then_tool_error_with_duration():
    async def execute(args, context):
        raise RuntimeError("boom")

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="explode",
                    description="Explode",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                )
            ]
        )
    )

    result = await runtime.execute(
        ToolCall(id="call-error", tool_id="explode", args={}),
        context=ToolContext(session_id="session-error", run_id="run-error"),
    )

    assert result.status == "error"
    assert result.success is False
    assert result.error == "boom"
    assert isinstance(result.metadata["duration_ms"], int)
    assert [event.type for event in result.events] == ["tool.started", "tool.error"]
    error_payload = result.events[-1].payload
    assert error_payload["error"] == "boom"
    assert error_payload["error_type"] == "RuntimeError"
    assert error_payload["status"] == "error"
    assert error_payload["success"] is False
    assert isinstance(error_payload["duration_ms"], int)
    assert error_payload["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_tool_supplied_events_are_preserved_between_started_and_completed():
    supplied_event = RuntimeEvent(
        type="tool.progress",
        message="Halfway.",
        payload={"step": 1},
    )

    async def execute(args, context):
        return ToolResult(
            call_id=context.tool_call_id or "call-events",
            tool_name=context.tool_name or "events",
            content="ok",
            events=[supplied_event],
        )

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="events",
                    description="Return events",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                )
            ]
        )
    )

    result = await runtime.execute(ToolCall(id="call-events", tool_id="events", args={}))

    assert [event.type for event in result.events] == [
        "tool.started",
        "tool.progress",
        "tool.completed",
    ]
    assert result.events[1] is supplied_event
    assert result.events[-1].payload["status"] == "success"
    assert result.events[-1].payload["success"] is True


@pytest.mark.asyncio
async def test_cancelled_context_stops_after_permission_before_execution():
    called = False

    async def execute(args, context):
        nonlocal called
        called = True
        return "should not run"

    runtime = ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="cancel_me",
                    description="Cancelled tool",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                )
            ]
        )
    )

    result = await runtime.execute(
        ToolCall(id="call-cancelled", tool_id="cancel_me", args={}),
        context=ToolContext(
            session_id="session-cancelled",
            run_id="run-cancelled",
            cancel_requested=lambda: True,
        ),
    )

    assert result.status == "cancelled"
    assert result.success is False
    assert result.error == "Tool execution cancelled."
    assert result.content == "Tool execution cancelled."
    assert called is False
    assert "cancel_requested" not in ToolContext(
        cancel_requested=lambda: True,
    ).to_metadata()
    assert result.events[0].type == "tool.cancelled"
    assert result.events[0].payload["tool_call_id"] == "call-cancelled"
    assert result.events[0].payload["run_id"] == "run-cancelled"


def test_registry_rejects_duplicate_tool_ids():
    async def execute(args, context):
        return "ok"

    tool = ToolDef(
        id="dup",
        description="Duplicate",
        input_schema={"type": "object", "properties": {}},
        execute=execute,
    )
    registry = ToolRegistry([tool])

    with pytest.raises(ValueError, match="Tool already registered"):
        registry.register(tool)
