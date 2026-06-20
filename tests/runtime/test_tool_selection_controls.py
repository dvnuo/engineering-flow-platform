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
from efp_runtime.tools.selection import (
    ToolSelection,
    resolve_model_aware_tool_selection,
    resolve_tool_selection,
)
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


def test_resolve_model_aware_tool_selection_does_not_run_without_model_hint():
    selection = resolve_model_aware_tool_selection(
        ["apply_patch", "edit", "write"],
        metadata={"model": "  "},
    )

    assert selection.enabled is True
    assert selection.ran is False
    assert selection.model_hint is None
    assert selection.mode == "none"
    assert selection.forced_disabled == ()


@pytest.mark.asyncio
async def test_model_hint_gpt_5_prefers_apply_patch_tool():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-gpt-5",
        metadata={"model": "gpt-5"},
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert _request_tool_ids(request) == ["apply_patch"]
    assert [schema.id for schema in request.provider_request.tools] == ["apply_patch"]
    assert request.metadata["model_aware_tool_selection"] == {
        "enabled": True,
        "ran": True,
        "model_hint": "gpt-5",
        "mode": "patch",
        "forced_disabled": ["edit", "write"],
    }
    assert request.metadata["tools"]["enabled"] == ["apply_patch"]
    assert request.metadata["tools"]["disabled"] == ["edit", "write"]


@pytest.mark.asyncio
async def test_model_hint_gpt_4_prefers_direct_file_tools():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-gpt-4",
        metadata={"model": "gpt-4.1"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert _request_tool_ids(provider.requests[0]) == ["edit", "write"]
    assert [schema.id for schema in provider.requests[0].provider_request.tools] == [
        "edit",
        "write",
    ]
    assert provider.requests[0].metadata["model_aware_tool_selection"]["mode"] == (
        "direct"
    )
    assert provider.requests[0].metadata[
        "model_aware_tool_selection_forced_disabled"
    ] == ["apply_patch"]


@pytest.mark.asyncio
async def test_model_hint_gpt_oss_prefers_direct_file_tools():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-gpt-oss",
        metadata={"model": "gpt-oss-120b"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert _request_tool_ids(provider.requests[0]) == ["edit", "write"]
    assert "apply_patch" not in [
        schema.id for schema in provider.requests[0].provider_request.tools
    ]


@pytest.mark.asyncio
async def test_without_model_hint_keeps_default_file_tool_selection():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run("run", session_id="session-no-model-hint")

    assert result.status == LoopStatus.COMPLETED
    assert _request_tool_ids(provider.requests[0]) == ["apply_patch"]
    assert provider.requests[0].metadata["model_aware_tool_selection"] == {
        "enabled": True,
        "ran": True,
        "model_hint": "gpt-5.4",
        "mode": "patch",
        "forced_disabled": ["edit", "write"],
    }


@pytest.mark.asyncio
async def test_model_aware_tool_selection_can_be_disabled_by_config():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=2,
            model_aware_tool_selection=False,
        ),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-selection-disabled",
        metadata={"model": "gpt-5"},
    )

    assert result.status == LoopStatus.COMPLETED
    assert _request_tool_ids(provider.requests[0]) == [
        "apply_patch",
        "edit",
        "write",
    ]
    assert provider.requests[0].metadata["model_aware_tool_selection"] == {
        "enabled": False,
        "ran": False,
        "model_hint": "gpt-5",
        "mode": "none",
        "forced_disabled": [],
    }


@pytest.mark.asyncio
async def test_per_run_override_cannot_reenable_model_disabled_tool():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-forced-disabled",
        metadata={"model": "gpt-5"},
        tools={"edit": True},
    )

    assert result.status == LoopStatus.COMPLETED
    assert _request_tool_ids(provider.requests[0]) == ["apply_patch"]
    assert "edit" in provider.requests[0].metadata["disabled_tool_ids"]


@pytest.mark.asyncio
async def test_structured_output_tool_remains_available_with_model_selection():
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2),
        tool_registry=_file_tool_registry(),
    )

    result = await runtime.run(
        "run",
        session_id="session-model-structured-output",
        metadata={"model": "gpt-5"},
        output_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    )

    assert result.status == LoopStatus.ERROR
    assert _request_tool_ids(provider.requests[0]) == [
        "StructuredOutput",
        "apply_patch",
    ]
    assert "StructuredOutput" in [
        schema.id for schema in provider.requests[0].provider_request.tools
    ]


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
        config=RuntimeConfig(max_iterations=2, disabled_tools=["beta"]),
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
        config=RuntimeConfig(max_iterations=2, enabled_tools=["alpha", "beta"]),
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


def _file_tool_registry() -> ToolRegistry:
    return ToolRegistry(_tool(tool_id) for tool_id in _FILE_TOOL_IDS)


def _request_tool_ids(request) -> list[str]:
    return [tool.id for tool in request.tools]


def _tool_call(call_id: str, tool_name: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": "{}",
        },
    }


_FILE_TOOL_IDS = ("apply_patch", "edit", "write")
