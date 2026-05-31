from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def _tool_call(
    call_id: str,
    *,
    tool_name: str = "echo",
    args: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(dict(args or {}), sort_keys=True),
        },
    }


def _tool_runtime(executions: list[dict[str, Any]]) -> ToolRuntime:
    async def execute(args, context):
        executions.append(
            {
                "tool_name": context.tool_name,
                "call_id": context.tool_call_id,
                "args": dict(args),
            }
        )
        return f"{context.tool_name}:{json.dumps(args, sort_keys=True)}"

    return ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id="echo",
                    description="Echo arguments",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                ),
                ToolDef(
                    id="other",
                    description="Other echo tool",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                ),
            ]
        )
    )


def _tool_result_call_ids(history) -> list[str]:
    return [
        part.tool_result.call_id
        for message in history
        for part in message.parts
        if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None
    ]


@pytest.mark.asyncio
async def test_two_repeated_tool_calls_do_not_trigger_default_threshold():
    executions: list[dict[str, Any]] = []
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "same"})]},
            {"content": "done"},
        ]
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=_tool_runtime(executions),
        max_iterations=3,
    )

    result = await runner.run(session_id="session-two", user_text="repeat twice")

    assert result.status == LoopStatus.COMPLETED
    assert result.pending_permission_request is None
    assert [item["call_id"] for item in executions] == ["call-1", "call-2"]
    assert _tool_result_call_ids(store.read_history("session-two")) == [
        "call-1",
        "call-2",
    ]


@pytest.mark.asyncio
async def test_third_repeated_tool_call_waits_for_doom_loop_permission():
    executions: list[dict[str, Any]] = []
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-3", args={"text": "same"})]},
            {"content": "after approval"},
        ]
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=_tool_runtime(executions),
        max_iterations=4,
        event_bus=bus,
    )

    result = await runner.run(session_id="session-doom", user_text="repeat")

    assert result.status == LoopStatus.WAITING_FOR_PERMISSION
    assert [item["call_id"] for item in executions] == ["call-1", "call-2"]
    history = store.read_history("session-doom")
    assert _tool_result_call_ids(history) == ["call-1", "call-2"]
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]

    request = result.pending_permission_request
    assert request is not None
    assert request["category"] == "doom_loop"
    assert request["resource"] == "echo"
    assert request["tool_id"] == "echo"
    assert request["risk"] == "medium"
    assert request["metadata"]["tool_name"] == "echo"
    assert request["metadata"]["repeat_count"] == 3
    assert request["metadata"]["arguments_json"] == '{"text":"same"}'
    assert request["metadata"]["patterns"] == ['{"text":"same"}']
    assert request["patterns"] == ['{"text":"same"}']

    permission_events = [
        event for event in bus.history("session-doom") if event.type == "tool.permission_requested"
    ]
    assert len(permission_events) == 1
    event_payload = permission_events[0].payload
    assert event_payload["tool_call_id"] == "call-3"
    assert event_payload["tool_name"] == "echo"
    assert event_payload["category"] == "doom_loop"
    assert event_payload["repeat_count"] == 3
    assert event_payload["permission_request"] == request
    assert bus.history("session-doom")[-1].type == "run_finish"
    assert bus.history("session-doom")[-1].payload["status"] == (
        LoopStatus.WAITING_FOR_PERMISSION
    )


@pytest.mark.asyncio
async def test_approve_then_resume_executes_pending_doom_loop_tool_call():
    executions: list[dict[str, Any]] = []
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-3", args={"text": "same"})]},
            {"content": "continued"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=4),
        tool_runtime=_tool_runtime(executions),
    )

    first = await runtime.run("repeat", session_id="session-approve-doom")
    runtime.approve_permission(first.pending_permission_request["request_id"])
    resumed = await runtime.resume("session-approve-doom")

    assert resumed.status == LoopStatus.COMPLETED
    assert [item["call_id"] for item in executions] == [
        "call-1",
        "call-2",
        "call-3",
    ]
    assert len(provider.requests) == 4
    assert [message.role for message in provider.requests[-1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]

    history = runtime.store.read_history("session-approve-doom")
    assert sum(1 for message in history if message.role is MessageRole.USER) == 1
    assert _tool_result_call_ids(history) == ["call-1", "call-2", "call-3"]
    assert history[-1].parts[0].text == "continued"


@pytest.mark.asyncio
async def test_deny_then_resume_appends_permission_denied_tool_result():
    executions: list[dict[str, Any]] = []
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-3", args={"text": "same"})]},
            {"content": "denied and continued"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=4),
        tool_runtime=_tool_runtime(executions),
    )

    first = await runtime.run("repeat", session_id="session-deny-doom")
    runtime.deny_permission(
        first.pending_permission_request["request_id"],
        reason="Stop repeated call.",
    )
    resumed = await runtime.resume("session-deny-doom")

    assert resumed.status == LoopStatus.COMPLETED
    assert [item["call_id"] for item in executions] == ["call-1", "call-2"]

    history = runtime.store.read_history("session-deny-doom")
    denial = [
        part.tool_result
        for message in history
        for part in message.parts
        if part.type is MessagePartType.TOOL_RESULT
        and part.tool_result is not None
        and part.tool_result.call_id == "call-3"
    ][0]
    assert denial.status == "permission_denied"
    assert denial.content == "Stop repeated call."
    assert denial.error == "Stop repeated call."
    assert denial.metadata["permission_category"] == "doom_loop"
    assert history[-1].parts[0].text == "denied and continued"


@pytest.mark.asyncio
async def test_different_args_or_tool_name_reset_doom_loop_sequence():
    executions: list[dict[str, Any]] = []
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "different"})]},
            {"tool_calls": [_tool_call("call-3", args={"text": "same"})]},
            {
                "tool_calls": [
                    _tool_call("call-4", tool_name="other", args={"text": "same"})
                ]
            },
            {"tool_calls": [_tool_call("call-5", args={"text": "same"})]},
            {"content": "done"},
        ]
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=_tool_runtime(executions),
        max_iterations=6,
    )

    result = await runner.run(session_id="session-reset", user_text="repeat with breaks")

    assert result.status == LoopStatus.COMPLETED
    assert result.pending_permission_request is None
    assert [item["call_id"] for item in executions] == [
        "call-1",
        "call-2",
        "call-3",
        "call-4",
        "call-5",
    ]


@pytest.mark.asyncio
async def test_doom_loop_threshold_none_disables_guard_through_runtime_config():
    executions: list[dict[str, Any]] = []
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-1", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-2", args={"text": "same"})]},
            {"tool_calls": [_tool_call("call-3", args={"text": "same"})]},
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=4, doom_loop_threshold=None),
        tool_runtime=_tool_runtime(executions),
    )

    result = await runtime.run("repeat", session_id="session-disabled")

    assert result.status == LoopStatus.COMPLETED
    assert result.pending_permission_request is None
    assert [item["call_id"] for item in executions] == [
        "call-1",
        "call-2",
        "call-3",
    ]


def test_doom_loop_threshold_must_be_at_least_two_when_enabled():
    with pytest.raises(ValueError, match="doom_loop_threshold"):
        RuntimeConfig(doom_loop_threshold=1)

    with pytest.raises(ValueError, match="doom_loop_threshold"):
        RuntimeLoopRunner(
            store=InMemorySessionStore(),
            provider=ScriptedLLMProvider([{"content": "unused"}]),
            tool_runtime=_tool_runtime([]),
            doom_loop_threshold=1,
        )


def test_doom_loop_guard_import_boundary():
    combined = "\n".join(
        [
            (ROOT / "src/efp_runtime/loop/runner.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/runtime/config.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/runtime/agent.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
