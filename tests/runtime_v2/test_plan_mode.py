from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.agents import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import MessagePartType, MessageRole
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOOL_IDS = [
    "apply_patch",
    "edit",
    "fetch",
    "glob",
    "grep",
    "invalid",
    "list_dir",
    "read",
    "read_file",
    "shell_exec",
    "shell_kill",
    "shell_status",
    "todo_write",
    "write",
    "write_file",
]
MUTATING_TOOL_IDS = {
    "apply_patch",
    "edit",
    "write",
    "write_file",
    "shell_exec",
    "shell_kill",
}


@pytest.mark.asyncio
async def test_build_mode_defaults_do_not_register_or_expose_plan_exit(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("Run normally.", session_id="session-build-mode")

    assert result.status == LoopStatus.COMPLETED
    assert create_core_tool_registry(tmp_path).ids() == DEFAULT_TOOL_IDS
    assert runtime.tool_runtime.registry.ids() == DEFAULT_TOOL_IDS
    assert [tool.id for tool in provider.requests[0].tools] == DEFAULT_TOOL_IDS
    assert [schema.id for schema in provider.requests[0].provider_request.tools] == (
        DEFAULT_TOOL_IDS
    )


@pytest.mark.asyncio
async def test_plan_mode_exposes_plan_exit_and_hides_mutating_tools(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            max_iterations=1,
        ),
    )

    result = await runtime.run("Plan the change.", session_id="session-plan-tools")

    assert result.status == LoopStatus.COMPLETED
    registry_ids = runtime.tool_runtime.registry.ids()
    assert "plan_exit" in registry_ids
    assert MUTATING_TOOL_IDS.issubset(set(registry_ids))

    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]
    assert "plan_exit" in request_tool_ids
    assert "plan_exit" in schema_ids
    assert MUTATING_TOOL_IDS.isdisjoint(request_tool_ids)
    assert MUTATING_TOOL_IDS.isdisjoint(schema_ids)


@pytest.mark.asyncio
async def test_plan_exit_is_terminal_and_does_not_invoke_provider_again(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    _tool_call(
                        "call-plan",
                        "plan_exit",
                        {
                            "plan": "1. Read the affected files.\n2. Implement tests.",
                            "summary": "Small scoped implementation.",
                            "next_steps": ["Confirm tests", "Implement patch"],
                            "risks": ["Hidden compatibility behavior"],
                        },
                    )
                ]
            }
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            max_iterations=4,
        ),
    )

    result = await runtime.run("Make a plan.", session_id="session-plan-exit")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 1
    assert len(provider.requests) == 1
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].type is MessagePartType.TOOL_CALL

    history = runtime.store.read_history("session-plan-exit")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
    ]
    tool_result = history[2].parts[0].tool_result
    assert tool_result is not None
    assert tool_result.tool_name == "plan_exit"
    assert tool_result.status == "success"
    assert tool_result.metadata["terminal"] is True
    assert tool_result.metadata["terminal_reason"] == "plan_exit"
    assert tool_result.output["status"] == "ready"
    assert "1. Read the affected files." in tool_result.content
    assert any(event.type == "tool_terminal" for event in result.runtime_events)
    assert result.runtime_events[-1].payload["terminal_reason"] == "plan_exit"


@pytest.mark.asyncio
async def test_enable_plan_tool_registers_plan_exit_in_build_mode(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            enable_plan_tool=True,
            max_iterations=1,
        ),
    )

    result = await runtime.run("Run with plan tool.", session_id="session-build-plan")

    assert result.status == LoopStatus.COMPLETED
    assert "plan_exit" in runtime.tool_runtime.registry.ids()
    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    assert "plan_exit" in request_tool_ids
    assert MUTATING_TOOL_IDS.issubset(set(request_tool_ids))


@pytest.mark.asyncio
async def test_plan_mode_read_only_false_does_not_force_hide_mutating_tools(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            plan_mode_read_only=False,
            max_iterations=1,
        ),
    )

    result = await runtime.run("Plan with full tools.", session_id="session-plan-write")

    assert result.status == LoopStatus.COMPLETED
    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    assert "plan_exit" in request_tool_ids
    assert MUTATING_TOOL_IDS.issubset(set(request_tool_ids))


@pytest.mark.asyncio
async def test_plan_mode_enabled_tools_cannot_bypass_read_only_policy(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            enabled_tools=["apply_patch", "plan_exit", "read_file"],
            disabled_tools=["read_file"],
            max_iterations=1,
        ),
    )

    result = await runtime.run("Plan with selected tools.", session_id="session-plan-enabled")

    assert result.status == LoopStatus.COMPLETED
    request_tool_ids = [tool.id for tool in provider.requests[0].tools]
    assert request_tool_ids == ["plan_exit"]
    assert provider.requests[0].metadata["disabled_tool_ids"] == [
        "apply_patch",
        "edit",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "list_dir",
        "read",
        "read_file",
        "shell_exec",
        "shell_kill",
        "shell_status",
        "todo_write",
        "write",
        "write_file",
    ]


@pytest.mark.asyncio
async def test_plan_mode_reminder_is_provider_only_context(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "planned"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            max_iterations=1,
        ),
    )

    result = await runtime.run("Plan only.", session_id="session-plan-reminder")

    assert result.status == LoopStatus.COMPLETED
    request_messages = provider.requests[0].provider_request.messages
    reminder_text = "\n".join(message.text for message in request_messages)
    assert "Plan mode is active" in reminder_text
    assert "plan_exit" in reminder_text
    assert provider.requests[0].metadata["runtime_mode"] == "plan"
    assert provider.requests[0].metadata["plan_mode_read_only"] is True

    history = runtime.store.read_history("session-plan-reminder")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all(message.role is not MessageRole.SYSTEM for message in history)


def test_child_config_preserves_plan_mode_settings(tmp_path: Path):
    base_config = RuntimeConfig(
        workspace_root=tmp_path,
        runtime_mode="plan",
        enable_plan_tool=True,
        plan_mode_read_only=False,
        enable_background_shell=False,
        background_shell_max_buffer_bytes=4096,
        max_iterations=2,
    )

    child = _child_config(
        profile=AgentProfile(name="general"),
        base_config=base_config,
        workspace_root=None,
        metadata={"task_id": "task-plan"},
    )

    assert child.runtime_mode == "plan"
    assert child.enable_plan_tool is True
    assert child.plan_mode_read_only is False
    assert child.enable_background_shell is False
    assert child.background_shell_max_buffer_bytes == 4096


def test_plan_mode_import_boundary():
    code = """
import importlib
import json
import sys

importlib.import_module("efp_runtime.runtime")
importlib.import_module("efp_runtime.tools.builtin.plan")

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

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
    )
    assert "from src.efp_runtime" not in combined
    assert "import src.efp_runtime" not in combined


def _tool_call(call_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(arguments, sort_keys=True),
        },
    }
