import pytest

from efp_runtime.models import ToolCall
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
        context=ToolContext(session_id="session-1"),
    )

    assert result.status == "success"
    assert result.truncated is True
    assert result.output == {"echo": "hello world", "session": "session-1"}
    assert result.metadata["original_chars"] > len(result.content)
    assert result.events[-1].type == "tool.completed"


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
                    id="write_file",
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

    result = await runtime.execute(ToolCall(id="call-1", tool_id="write_file", args={}))

    assert result.status == "permission_requested"
    assert result.metadata["permission_request"]["request_id"].startswith("perm_")
    assert result.metadata["permission_request"]["tool_id"] == "write_file"
    assert result.metadata["permission_request"]["action"] == "ask"
    assert result.metadata["permission_request"]["category"] == "filesystem"
    assert result.metadata["permission_request"]["reason"] == "Writes require approval."
    assert called is False


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
