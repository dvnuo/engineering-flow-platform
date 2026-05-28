from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessagePart, MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.tools.selection import ToolSelection, resolve_tool_selection
from efp_runtime.types import ToolCall


ROOT = Path(__file__).resolve().parents[2]


def test_resolve_tool_selection_applies_sorted_overrides_and_rejects_unknown():
    assert resolve_tool_selection(
        ["beta", "alpha", "gamma"],
        enabled=["gamma", "alpha"],
        disabled=["gamma"],
        overrides={"beta": True},
    ) == ["alpha", "beta"]

    with pytest.raises(KeyError, match="missing"):
        resolve_tool_selection(["alpha"], overrides={"missing": False})


@pytest.mark.asyncio
async def test_provider_request_only_contains_enabled_tools():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([_tool("alpha"), _tool("beta")])),
        tool_selection=ToolSelection(enabled={"beta"}),
    )

    result = await runner.run(session_id="session-enabled", user_text="run")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert [tool.id for tool in request.tools] == ["beta"]
    assert [schema.id for schema in request.provider_request.tools] == ["beta"]
    assert request.metadata["enabled_tool_ids"] == ["beta"]
    assert request.metadata["disabled_tool_ids"] == ["alpha"]


@pytest.mark.asyncio
async def test_config_disabled_tool_is_not_shown_to_provider():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1, disabled_tools=["beta"]),
        tool_registry=ToolRegistry([_tool("alpha"), _tool("beta")]),
    )

    result = await runtime.run("run", session_id="session-config-disabled")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert [tool.id for tool in request.tools] == ["alpha"]
    assert [schema.id for schema in request.provider_request.tools] == ["alpha"]
    assert request.metadata["tools"]["enabled"] == ["alpha"]
    assert request.metadata["tools"]["disabled"] == ["beta"]


@pytest.mark.asyncio
async def test_per_run_override_disables_config_enabled_tool():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1, enabled_tools=["alpha", "beta"]),
        tool_registry=ToolRegistry([_tool("alpha"), _tool("beta")]),
    )

    result = await runtime.run(
        "run",
        session_id="session-run-override",
        tools={"beta": False},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert [tool.id for tool in request.tools] == ["alpha"]
    assert [schema.id for schema in request.provider_request.tools] == ["alpha"]
    assert request.metadata["enabled_tool_ids"] == ["alpha"]
    assert request.metadata["disabled_tool_ids"] == ["beta"]


@pytest.mark.asyncio
async def test_unknown_per_run_override_id_raises_key_error():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    runtime = AgentRuntime(
        provider=provider,
        tool_registry=ToolRegistry([_tool("alpha")]),
    )

    with pytest.raises(KeyError, match="missing"):
        await runtime.run(
            "run",
            session_id="session-unknown-override",
            tools={"missing": False},
        )

    assert provider.requests == []


@pytest.mark.asyncio
async def test_disabled_tool_call_appends_error_result_and_continues_provider():
    called: list[dict[str, Any]] = []

    async def execute(args, context):
        called.append({"args": args, "session_id": context.session_id})
        return "should not run"

    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-disabled", "echo")]},
            {"content": "continued after disabled result"},
        ]
    )
    store = InMemorySessionStore()
    runner = RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry([_tool("echo", execute=execute)])),
        tool_selection=ToolSelection(disabled={"echo"}),
        max_iterations=3,
    )

    result = await runner.run(session_id="session-disabled-call", user_text="run echo")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 2
    assert called == []
    assert len(provider.requests) == 2
    assert [tool.id for tool in provider.requests[0].tools] == []
    assert provider.requests[1].messages[-1].role is MessageRole.TOOL

    history = store.read_history("session-disabled-call")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    disabled = history[2].parts[0].tool_result
    assert disabled is not None
    assert disabled.call_id == "call-disabled"
    assert disabled.tool_name == "echo"
    assert disabled.status == "disabled"
    assert disabled.success is False
    assert disabled.error == "Tool is disabled: echo"
    assert any(event.type == "tool.disabled" for event in result.runtime_events)


@pytest.mark.asyncio
async def test_resume_uses_tool_selection_without_appending_user_message():
    called: list[dict[str, Any]] = []

    async def execute(args, context):
        called.append({"args": args, "session_id": context.session_id})
        return "should not run"

    provider = ScriptedLLMProvider([{"content": "handled disabled pending call"}])
    runtime = AgentRuntime(
        provider=provider,
        max_iterations=2,
        tool_registry=ToolRegistry([_tool("echo", execute=execute)]),
    )
    runtime.store.create_session(session_id="session-resume-selection")
    runtime.store.append_message(
        "session-resume-selection",
        role=MessageRole.USER,
        parts=[MessagePart.text_part("Use echo.")],
        status="complete",
    )
    runtime.store.append_message(
        "session-resume-selection",
        role=MessageRole.ASSISTANT,
        parts=[
            MessagePart.tool_call_part(
                ToolCall(id="call-resume-disabled", tool_id="echo", args={})
            )
        ],
        status="complete",
    )

    result = await runtime.resume(
        "session-resume-selection",
        tools={"echo": False},
    )

    assert result.status == LoopStatus.COMPLETED
    assert called == []
    assert len(provider.requests) == 1
    assert [tool.id for tool in provider.requests[0].tools] == []

    history = runtime.store.read_history("session-resume-selection")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    assert sum(1 for message in history if message.role is MessageRole.USER) == 1
    result_part = history[2].parts[0]
    assert result_part.type is MessagePartType.TOOL_RESULT
    assert result_part.tool_result is not None
    assert result_part.tool_result.status == "disabled"


def test_tool_selection_import_boundary():
    code = """
import importlib
import json
import sys

importlib.import_module("efp_runtime.tools.selection")
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


def _tool(
    tool_id: str,
    *,
    execute=None,
) -> ToolDef:
    async def default_execute(args, context):
        return {"tool_id": tool_id, "args": args, "session_id": context.session_id}

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        execute=execute or default_execute,
    )


def _tool_call(call_id: str, tool_name: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": "{}",
        },
    }
