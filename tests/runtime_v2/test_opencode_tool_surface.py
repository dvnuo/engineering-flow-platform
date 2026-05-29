from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime.agents import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.opencode_parity import DEFAULT_CORE_TOOL_IDS
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry


REMOVED_TOOL_IDS = {
    "fetch",
    "list_dir",
    "read_file",
    "shell_exec",
    "shell_kill",
    "shell_status",
    "skill_list",
    "task_cancel",
    "task_status",
    "todo_write",
    "write_file",
}


def test_default_core_registry_uses_opencode_tool_surface(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert registry.ids() == list(DEFAULT_CORE_TOOL_IDS)
    assert REMOVED_TOOL_IDS.isdisjoint(registry.ids())


@pytest.mark.asyncio
async def test_agent_runtime_default_request_uses_opencode_core_tools(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("List tools.", session_id="session-default-surface")
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    expected_model_tools = [
        tool_id for tool_id in DEFAULT_CORE_TOOL_IDS if tool_id not in {"edit", "write"}
    ]
    assert schema_ids == expected_model_tools
    assert REMOVED_TOOL_IDS.isdisjoint(schema_ids)


@pytest.mark.asyncio
async def test_model_aware_selection_uses_only_registered_file_tools_by_default(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run(
        "Patch this.",
        session_id="session-model-aware-surface",
        metadata={"model": "gpt-5"},
    )
    selection = provider.requests[0].metadata["model_aware_tool_selection"]

    assert result.status == LoopStatus.COMPLETED
    assert selection["mode"] == "patch"
    assert selection["forced_disabled"] == ["edit", "write"]
    assert set(provider.requests[0].metadata["disabled_tool_ids"]) == {"edit", "write"}


def test_child_config_inherits_only_opencode_tool_fields(tmp_path: Path):
    base = RuntimeConfig(workspace_root=tmp_path, disabled_tools=["write"])

    child = _child_config(
        profile=AgentProfile(name="review"),
        base_config=base,
        workspace_root=None,
        metadata={},
    )

    assert child.disabled_tools == ["write"]
    assert not hasattr(child, "tool_surface")
    assert not hasattr(child, "include_legacy_tool_aliases")
    assert not hasattr(child, "enable_local_python_tools")
    assert not hasattr(child, "local_tool_directories")
