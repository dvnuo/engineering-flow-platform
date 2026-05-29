from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime.agents import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.opencode_parity import DEFAULT_CORE_TOOL_IDS, LEGACY_ALIAS_TOOL_IDS
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry


OPENCODE_TOOL_IDS = list(DEFAULT_CORE_TOOL_IDS)
LEGACY_ALIAS_IDS = set(LEGACY_ALIAS_TOOL_IDS)


def test_default_core_registry_uses_opencode_tool_surface(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert registry.ids() == OPENCODE_TOOL_IDS
    assert LEGACY_ALIAS_IDS.isdisjoint(registry.ids())


def test_core_registry_can_opt_into_legacy_aliases(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path, include_legacy_aliases=True)

    assert set(OPENCODE_TOOL_IDS).issubset(registry.ids())
    assert {
        "fetch",
        "list_dir",
        "read_file",
        "shell_exec",
        "shell_kill",
        "shell_status",
        "todo_write",
        "write_file",
    }.issubset(registry.ids())


def test_legacy_tool_surface_enables_legacy_aliases(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path, tool_surface="legacy")

    assert "read_file" in registry.ids()
    assert "shell_exec" in registry.ids()
    assert "todo_write" in registry.ids()


@pytest.mark.asyncio
async def test_agent_runtime_default_request_hides_legacy_aliases(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("List tools.", session_id="session-default-surface")
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    assert schema_ids == OPENCODE_TOOL_IDS
    assert LEGACY_ALIAS_IDS.isdisjoint(schema_ids)
    assert runtime.config.tool_surface == "opencode"
    assert runtime.config.include_legacy_tool_aliases is False


@pytest.mark.asyncio
async def test_agent_runtime_legacy_config_request_includes_legacy_aliases(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            tool_surface="legacy",
        ),
    )

    result = await runtime.run("List tools.", session_id="session-legacy-surface")
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    assert set(OPENCODE_TOOL_IDS).issubset(schema_ids)
    assert {"fetch", "read_file", "shell_exec", "todo_write", "write_file"}.issubset(
        schema_ids
    )
    assert runtime.config.include_legacy_tool_aliases is True


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
    assert "write_file" not in provider.requests[0].metadata["disabled_tool_ids"]


def test_child_config_inherits_tool_surface_fields(tmp_path: Path):
    base = RuntimeConfig(
        workspace_root=tmp_path,
        tool_surface="legacy",
        include_legacy_tool_aliases=True,
        enable_local_python_tools=True,
        local_tool_directories=[tmp_path / "tools"],
    )

    child = _child_config(
        profile=AgentProfile(name="review"),
        base_config=base,
        workspace_root=None,
        metadata={},
    )

    assert child.tool_surface == "legacy"
    assert child.include_legacy_tool_aliases is True
    assert child.enable_local_python_tools is True
    assert child.local_tool_directories == [tmp_path / "tools"]
