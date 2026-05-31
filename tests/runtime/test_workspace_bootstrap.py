from __future__ import annotations

import json
from pathlib import Path

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.workspace import (
    create_agent_runtime_from_workspace,
    load_runtime_workspace,
)


@pytest.mark.asyncio
async def test_workspace_runtime_loads_config_command_and_agent_registry(
    tmp_path: Path,
):
    _write_json(
        tmp_path / "opencode.json",
        {
            "command": {
                "audit": {
                    "template": "Audit $ARGUMENTS.",
                    "agent": "review",
                },
            },
            "agents": {
                "review": {
                    "description": "Reviews changes",
                    "prompt": "Use the review profile.",
                },
            },
        },
    )
    provider = ScriptedLLMProvider([{"content": "Audited."}])

    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=tmp_path,
    )
    result = await runtime.run("/audit src", session_id="session-workspace-command")

    assert result.status == LoopStatus.COMPLETED
    assert runtime.provider is provider
    assert runtime.command_registry is not None
    assert runtime.agent_registry is not None
    request = provider.requests[0]
    assert request.metadata["command_name"] == "audit"
    assert request.metadata["selected_agent_source"] == "command"
    assert request.metadata["agent_name"] == "review"
    assert request.metadata["agent_description"] == "Reviews changes"
    assert any(
        message.role == "system" and message.text == "Use the review profile."
        for message in request.provider_request.messages
    )
    assert "Audit src." in request.provider_request.messages[-1].text


@pytest.mark.asyncio
async def test_workspace_default_agent_is_passed_to_agent_runtime(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "defaultAgent": "review",
            "agents": {
                "general": {"prompt": "Use the general profile."},
                "review": {"prompt": "Use the review profile."},
            },
        },
    )
    provider = ScriptedLLMProvider([{"content": "Reviewed."}])

    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=tmp_path,
    )
    await runtime.run("Check this.", session_id="session-default-agent")

    assert runtime.default_agent == "review"
    request = provider.requests[0]
    assert request.metadata["selected_agent_source"] == "default"
    assert request.metadata["agent_name"] == "review"


@pytest.mark.asyncio
async def test_workspace_explicit_default_agent_overrides_loaded_default(
    tmp_path: Path,
):
    _write_json(
        tmp_path / "opencode.json",
        {
            "defaultAgent": "general",
            "agents": {
                "general": {"prompt": "Use the general profile."},
                "debug": {"prompt": "Use the debug profile."},
            },
        },
    )
    provider = ScriptedLLMProvider([{"content": "Debugged."}])

    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=tmp_path,
        default_agent="debug",
    )
    await runtime.run("Investigate.", session_id="session-override-default-agent")

    assert runtime.default_agent == "debug"
    assert provider.requests[0].metadata["agent_name"] == "debug"


def test_workspace_runtime_requires_injected_provider(tmp_path: Path):
    with pytest.raises(TypeError, match="provider"):
        create_agent_runtime_from_workspace(workspace_root=tmp_path)


def test_workspace_load_options_forward_paths_and_include_defaults(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "defaultAgent": "general",
            "agents": {"general": {"prompt": "Default profile."}},
        },
    )
    _write_json(
        tmp_path / "custom.json",
        {
            "default_agent": "custom",
            "agents": {"custom": {"prompt": "Custom profile."}},
            "command": {"custom": "Custom command."},
        },
    )

    workspace = load_runtime_workspace(
        tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )
    provider = ScriptedLLMProvider([{"content": "Custom."}])
    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )

    assert workspace.workspace_root == tmp_path.resolve()
    assert workspace.config is workspace.load_result.config
    assert workspace.agent_registry is workspace.load_result.agent_registry
    assert workspace.command_registry is workspace.load_result.command_registry
    assert workspace.load_result.loaded_paths == [(tmp_path / "custom.json").resolve()]
    assert runtime.agent_registry is not None
    assert runtime.agent_registry.names() == ["custom"]
    assert runtime.default_agent == "custom"
    assert runtime.command_registry is not None
    assert runtime.command_registry.get("custom") is not None


def test_load_runtime_workspace_resolves_parent_root_from_nested_dir(tmp_path: Path):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    _write_json(
        project / "opencode.json",
        {"command": {"audit": "Audit $ARGUMENTS."}},
    )

    workspace = load_runtime_workspace(nested)

    assert workspace.workspace_root == project.resolve()
    assert workspace.config.workspace_root == project.resolve()
    assert workspace.load_result.loaded_paths == [
        (project / "opencode.json").resolve(),
    ]
    assert workspace.command_registry is not None
    assert workspace.command_registry.get("audit").content == "Audit $ARGUMENTS."


def test_create_agent_runtime_from_workspace_resolves_parent_root_from_nested_dir(
    tmp_path: Path,
):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    _write_json(project / "opencode.json", {"runtime_mode": "plan"})
    provider = ScriptedLLMProvider([{"content": "Done."}])

    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=nested,
    )

    assert runtime.config.workspace_root == project.resolve()
    assert runtime.config.runtime_mode == "plan"
    assert runtime.workspace_snapshot_store is not None
    assert runtime.workspace_snapshot_store.workspace_root == project.resolve()


@pytest.mark.asyncio
async def test_workspace_metadata_merges_through_agent_runtime_behavior(
    tmp_path: Path,
):
    _write_json(
        tmp_path / "runtime.json",
        {
            "project": "alpha",
        },
    )
    provider = ScriptedLLMProvider([{"content": "Metadata."}])

    runtime = create_agent_runtime_from_workspace(
        provider=provider,
        workspace_root=tmp_path,
        paths=["runtime.json"],
        include_defaults=False,
        max_iterations=1,
        metadata={"suite": "workspace"},
    )
    await runtime.run(
        "Track metadata.",
        session_id="session-workspace-metadata",
        metadata={"request_id": "run-1"},
    )

    request = provider.requests[0]
    assert request.metadata["suite"] == "workspace"
    assert request.metadata["request_id"] == "run-1"
    assert request.metadata["max_iterations"] == 1
    assert request.metadata["raw_config"] == {"project": "alpha"}
    assert request.metadata["unconsumed_config"] == {"project": "alpha"}


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
