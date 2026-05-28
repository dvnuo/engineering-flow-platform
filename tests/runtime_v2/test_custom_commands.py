from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.agents import AgentProfile, AgentRegistry
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.commands import (
    CommandDefinition,
    CommandRegistry,
    builtin_command_definitions,
    command_definitions_from_config,
    expand_command,
)
from efp_runtime.config_loader import load_runtime_config
from efp_runtime.llm.provider import OpenAICompatibleProvider, RecordingTransport
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.runtime.agent import _resolve_config
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[2]


def test_command_registry_discovers_and_parses_command_files(tmp_path: Path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / ".opencode" / "command"
    hidden_dir = second_dir / ".hidden"
    nested_dir = second_dir / "review"
    first_dir.mkdir()
    second_dir.mkdir(parents=True)
    hidden_dir.mkdir()
    nested_dir.mkdir()

    (first_dir / "fix.md").write_text("Old fix prompt.", encoding="utf-8")
    (second_dir / "fix.md").write_text(
        "---\n"
        "name: fix\n"
        "description: Fix defects\n"
        "argument-hint: <issue>\n"
        "agent: coder\n"
        "model: gpt-test\n"
        "subtask: false\n"
        "tools: [read_file, edit]\n"
        "---\n"
        "# Fix\nApply the smallest safe change.\n",
        encoding="utf-8",
    )
    (nested_dir / "pr.txt").write_text(
        "description: Review a PR\n"
        "argument-hint: <pr>\n"
        "# Review\nFocus on correctness.\n",
        encoding="utf-8",
    )
    (hidden_dir / "secret.md").write_text("Hidden.", encoding="utf-8")

    registry = CommandRegistry([first_dir, second_dir])
    commands = {command.name: command for command in registry.discover()}

    assert sorted(commands) == ["fix", "review:pr"]
    assert commands["fix"].description == "Fix defects"
    assert commands["fix"].content == "# Fix\nApply the smallest safe change."
    assert commands["fix"].metadata["argument-hint"] == "<issue>"
    assert commands["fix"].metadata["agent"] == "coder"
    assert commands["fix"].metadata["model"] == "gpt-test"
    assert commands["fix"].metadata["subtask"] is False
    assert commands["fix"].subtask is False
    assert commands["fix"].source == "file"
    assert commands["fix"].metadata["tools"] == ["read_file", "edit"]
    assert commands["fix"].command_file == second_dir / "fix.md"
    assert commands["review:pr"].description == "Review a PR"
    assert commands["review:pr"].content == "# Review\nFocus on correctness."


def test_config_command_definitions_parse_string_and_mapping_forms():
    definitions = command_definitions_from_config(
        {
            "command": {
                "test": {
                    "template": "Run tests for $ARGUMENTS",
                    "description": "Run tests",
                    "argument-hint": "<target>",
                    "agent": "build",
                    "model": "provider/model",
                    "tools": ["shell_exec"],
                    "subtask": False,
                },
                "review": "Review $1",
                "renamed": {
                    "name": "/audit",
                    "content": "Audit $ARGUMENTS",
                },
            }
        }
    )

    commands = {definition.name: definition for definition in definitions}
    assert sorted(commands) == ["audit", "review", "test"]
    assert commands["test"].content == "Run tests for $ARGUMENTS"
    assert commands["test"].description == "Run tests"
    assert commands["test"].argument_hint == "<target>"
    assert commands["test"].agent == "build"
    assert commands["test"].model == "provider/model"
    assert commands["test"].subtask is False
    assert commands["test"].metadata["tools"] == ["shell_exec"]
    assert commands["test"].source == "config"
    assert commands["review"].content == "Review $1"
    assert commands["audit"].content == "Audit $ARGUMENTS"


def test_config_command_requires_template_or_content():
    with pytest.raises(ValueError, match="missing"):
        command_definitions_from_config(
            {"command": {"missing": {"description": "No template"}}}
        )


def test_command_and_commands_aliases_override_in_stable_order():
    first = CommandRegistry.from_sources(
        definitions=command_definitions_from_config(
            {
                "command": {"dup": "from command", "only": "one"},
                "commands": {"dup": "from commands"},
            }
        )
    )
    second = CommandRegistry.from_sources(
        definitions=command_definitions_from_config(
            {
                "commands": {"dup": "from commands"},
                "command": {"dup": "from command"},
            }
        )
    )

    assert first.get("dup").content == "from commands"
    assert first.get("only").content == "one"
    assert second.get("dup").content == "from command"


def test_builtin_command_templates_keep_arguments_and_dot_workspace_root():
    commands = {
        command.name: command for command in builtin_command_definitions(None)
    }

    assert commands["init"].source == "builtin"
    assert "Workspace root: ." in commands["init"].content
    assert "Target file: ./AGENTS.md" in commands["init"].content
    assert "$ARGUMENTS" in commands["init"].content
    assert commands["review"].source == "builtin"
    assert commands["review"].subtask is True
    assert "git show $ARGUMENTS" in commands["review"].content
    assert "git diff $ARGUMENTS...HEAD" in commands["review"].content


def test_markdown_file_command_overrides_config_command(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "File command.")
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="fix",
                content="Config command.",
                source="config",
            )
        ],
        command_directories=[command_dir],
    )

    command = registry.get("fix")

    assert command is not None
    assert command.content == "File command."
    assert command.source == "file"


def test_command_template_variables_render_arguments_only():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="tmpl",
                content=(
                    "all=$ARGUMENTS\n"
                    "one=$1\n"
                    "two=$2\n"
                    "missing=$3\n"
                    "home=$HOME\n"
                    "joined=$1/$2"
                ),
                source="config",
            )
        ]
    )

    expansion = expand_command('/tmpl alpha "beta gamma"', registry)

    assert expansion is not None
    assert "all=alpha \"beta gamma\"" in expansion.text
    assert "one=alpha" in expansion.text
    assert "two=beta gamma" in expansion.text
    assert "missing=\n" in expansion.text
    assert "home=$HOME" in expansion.text
    assert "joined=alpha/beta gamma" in expansion.text


@pytest.mark.asyncio
async def test_slash_command_expands_into_provider_user_message(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nApply this fix.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/fix ticket-123", session_id="session-command")

    request = provider.requests[0]
    user_message = request.provider_request.messages[0]
    assert user_message.role == "user"
    assert '<command name="fix"' in user_message.text
    assert "# Fix\nApply this fix." in user_message.text
    assert "<command_arguments>\nticket-123\n</command_arguments>" in user_message.text
    assert request.metadata["command_name"] == "fix"
    assert request.metadata["command_file"] == str(command_dir / "fix.md")
    assert request.metadata["command_arguments"] == "ticket-123"
    assert request.metadata["command_source"] == "file"
    assert request.provider_request.metadata["command_name"] == "fix"


@pytest.mark.asyncio
async def test_builtin_init_available_without_config_or_command_directory(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/init prefer pytest", session_id="session-builtin-init")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    root = str(tmp_path.resolve())
    assert request.metadata["command_name"] == "init"
    assert request.metadata["command_source"] == "builtin"
    assert request.metadata["command_file"] == ""
    assert root in text
    assert f"{root}/AGENTS.md" in text
    assert "prefer pytest" in text


@pytest.mark.asyncio
async def test_builtin_review_available_without_config_or_command_directory(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/review feature/rework", session_id="session-builtin-review")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert request.metadata["command_name"] == "review"
    assert request.metadata["command_source"] == "builtin"
    assert request.metadata["command_subtask"] is True
    assert "Review the requested code changes." in text
    assert "git diff" in text
    assert "git diff --cached" in text
    assert "git diff feature/rework...HEAD" in text
    assert "feature/rework" in text
    assert "Report findings first" in text


@pytest.mark.asyncio
async def test_config_command_overrides_builtin_init(tmp_path: Path):
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "command": {
                    "init": {
                        "template": "Config init $ARGUMENTS",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    loaded = load_runtime_config(tmp_path)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=loaded.config,
        command_registry=loaded.command_registry,
    )

    await runtime.run("/init docs", session_id="session-config-init")

    request = provider.requests[0]
    text = _last_user_text(request)
    assert request.metadata["command_name"] == "init"
    assert request.metadata["command_source"] == "config"
    assert "Config init docs" in text
    assert "Create or update the workspace agent guide." not in text


@pytest.mark.asyncio
async def test_markdown_command_overrides_builtin_review(tmp_path: Path):
    command_file = tmp_path / ".opencode" / "commands" / "review.md"
    command_file.parent.mkdir(parents=True)
    command_file.write_text("File review $ARGUMENTS", encoding="utf-8")
    loaded = load_runtime_config(tmp_path)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=loaded.config,
        command_registry=loaded.command_registry,
    )

    await runtime.run("/review branch-name", session_id="session-file-review")

    request = provider.requests[0]
    text = _last_user_text(request)
    assert request.metadata["command_name"] == "review"
    assert request.metadata["command_source"] == "file"
    assert request.metadata["command_file"] == str(command_file)
    assert "File review branch-name" in text
    assert "Review the requested code changes." not in text


@pytest.mark.asyncio
async def test_injected_command_registry_expands_without_config_directories():
    definition = CommandDefinition(
        name="build",
        content="Build $1 with $ARGUMENTS.",
        source="config",
        agent="builder",
        model="provider/model",
        subtask=False,
        metadata={"tools": ["shell_exec"]},
    )
    registry = CommandRegistry.from_sources(definitions=[definition])
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        agent_registry=AgentRegistry(
            [AgentProfile(name="builder")],
            default_agent=None,
        ),
        tool_registry=ToolRegistry([_tool("shell_exec")]),
    )

    assert runtime.command_registry is registry
    assert runtime.command_registry.get("init") is None

    await runtime.run("/build target --fast", session_id="session-injected-command")
    definition.metadata["tools"].append("write_file")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert "Build target with target --fast." in text
    assert request.metadata["command_name"] == "build"
    assert request.metadata["command_file"] == ""
    assert request.metadata["command_source"] == "config"
    assert request.metadata["command_agent"] == "builder"
    assert request.metadata["command_model"] == "provider/model"
    assert request.metadata["command_subtask"] is False
    assert request.metadata["command_metadata"]["tools"] == ["shell_exec"]


@pytest.mark.asyncio
async def test_command_agent_selects_profile_when_caller_omits_agent():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="audit",
                content="Audit $ARGUMENTS.",
                source="config",
                agent="review",
            )
        ]
    )
    agent_registry = AgentRegistry(
        [
            AgentProfile(name="debugger", prompt="Use the debugger profile."),
            AgentProfile(
                name="review",
                description="Reviews code",
                prompt="Use the review profile.",
            ),
        ],
        default_agent=None,
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        agent_registry=agent_registry,
    )

    await runtime.run("/audit src", session_id="session-command-agent")

    request = provider.requests[0]
    assert request.metadata["command_agent"] == "review"
    assert request.metadata["selected_agent_source"] == "command"
    assert request.metadata["agent_name"] == "review"
    assert request.metadata["agent_description"] == "Reviews code"
    assert request.provider_request.messages[0].text == "Use the review profile."
    assert "Audit src." in request.provider_request.messages[1].text


@pytest.mark.asyncio
async def test_caller_agent_wins_over_command_agent():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="audit",
                content="Audit.",
                source="config",
                agent="review",
            )
        ]
    )
    agent_registry = AgentRegistry(
        [
            AgentProfile(name="debugger", prompt="Use the debugger profile."),
            AgentProfile(name="review", prompt="Use the review profile."),
        ],
        default_agent=None,
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        agent_registry=agent_registry,
    )

    await runtime.run(
        "/audit",
        session_id="session-caller-agent-wins",
        agent="debugger",
    )

    request = provider.requests[0]
    assert request.metadata["command_agent"] == "review"
    assert request.metadata["selected_agent_source"] == "caller"
    assert request.metadata["agent_name"] == "debugger"
    assert request.provider_request.messages[0].text == "Use the debugger profile."


@pytest.mark.asyncio
async def test_unknown_command_agent_raises_before_provider_request():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="audit",
                content="Audit.",
                source="config",
                agent="missing",
            )
        ]
    )
    agent_registry = AgentRegistry(
        [AgentProfile(name="general"), AgentProfile(name="review")],
        default_agent=None,
    )
    provider = ScriptedLLMProvider([{"content": "unused"}])
    runtime = AgentRuntime(
        provider=provider,
        command_registry=registry,
        agent_registry=agent_registry,
    )

    with pytest.raises(KeyError) as error:
        await runtime.run("/audit", session_id="session-command-missing-agent")

    error_text = str(error.value)
    assert "missing" in error_text
    assert "general" in error_text
    assert "review" in error_text
    assert provider.requests == []


@pytest.mark.asyncio
async def test_command_model_records_requested_model_without_provider_model_switch():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="build",
                content="Build.",
                source="config",
                model="provider/model",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run("/build", session_id="session-command-model")

    request = provider.requests[0]
    assert request.metadata["command_model"] == "provider/model"
    assert request.metadata["requested_model"] == "provider/model"
    assert request.provider_request.metadata["requested_model"] == "provider/model"
    assert not hasattr(request.provider_request, "model")


@pytest.mark.asyncio
async def test_command_model_sets_openai_payload_model_without_provider_model_switch():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="build",
                content="Build.",
                source="config",
                model="provider/model",
            )
        ]
    )
    transport = RecordingTransport(
        [
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Done."},
                        "finish_reason": "stop",
                    }
                ]
            }
        ]
    )
    provider = OpenAICompatibleProvider(model="base/model", transport=transport)
    requests: list[Any] = []
    original_invoke = provider.invoke

    async def recording_invoke(request):
        requests.append(request)
        return await original_invoke(request)

    provider.invoke = recording_invoke
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run("/build", session_id="session-command-openai-model")

    request = requests[0]
    assert provider.model == "base/model"
    assert transport.payloads[0]["model"] == "provider/model"
    assert request.metadata["command_model"] == "provider/model"
    assert request.metadata["requested_model"] == "provider/model"
    assert request.provider_request.metadata["requested_model"] == "provider/model"
    assert not hasattr(request.provider_request, "model")


@pytest.mark.asyncio
async def test_command_tools_merge_profile_command_and_caller_overrides():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="listtools",
                content="Use listed tools.",
                source="config",
                metadata={"tools": ["read_file", "edit"]},
            ),
            CommandDefinition(
                name="maptools",
                content="Use mapped tools.",
                source="config",
                metadata={
                    "tools": {
                        "read_file": True,
                        "edit": False,
                        "shell_exec": True,
                    }
                },
            ),
        ]
    )
    profile = AgentProfile(
        name="limited",
        tools={"read_file": False, "edit": False, "shell_exec": False},
    )
    provider = ScriptedLLMProvider(
        [
            {"content": "List tools."},
            {"content": "Mapped tools."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1),
        command_registry=registry,
        tool_registry=ToolRegistry(
            [_tool("read_file"), _tool("edit"), _tool("shell_exec")]
        ),
    )

    await runtime.run(
        "/listtools",
        session_id="session-command-list-tools",
        agent=profile,
        tools={"edit": False},
    )
    await runtime.run(
        "/maptools",
        session_id="session-command-map-tools",
        agent=profile,
        tools={"edit": True, "shell_exec": False},
    )

    assert [tool.id for tool in provider.requests[0].tools] == ["read_file"]
    assert provider.requests[0].metadata["enabled_tool_ids"] == ["read_file"]
    assert provider.requests[0].metadata["disabled_tool_ids"] == [
        "edit",
        "shell_exec",
    ]
    assert [tool.id for tool in provider.requests[1].tools] == ["edit", "read_file"]
    assert provider.requests[1].metadata["enabled_tool_ids"] == [
        "edit",
        "read_file",
    ]
    assert provider.requests[1].metadata["disabled_tool_ids"] == ["shell_exec"]
    assert profile.tools == {
        "read_file": False,
        "edit": False,
        "shell_exec": False,
    }


@pytest.mark.asyncio
async def test_invalid_command_tools_raise_before_provider_request():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="badtools",
                content="Bad tools.",
                source="config",
                metadata={"tools": "read_file"},
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "unused"}])
    runtime = AgentRuntime(
        provider=provider,
        command_registry=registry,
        tool_registry=ToolRegistry([_tool("read_file")]),
    )

    with pytest.raises(ValueError, match="command tools metadata"):
        await runtime.run("/badtools", session_id="session-invalid-command-tools")

    assert provider.requests == []


@pytest.mark.asyncio
async def test_slash_command_preserves_remaining_body(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nUse details.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run(
        "/fix bug-7\nextra details\nsecond line",
        session_id="session-command-body",
    )

    text = provider.requests[0].provider_request.messages[0].text
    assert "<command_arguments>\nbug-7\n</command_arguments>" in text
    assert "<command_input>\nextra details\nsecond line\n</command_input>" in text


@pytest.mark.asyncio
async def test_unknown_slash_command_is_left_as_user_text(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nKnown.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    original = "/missing foo\nkeep this body"
    await runtime.run(original, session_id="session-unknown-command")

    request = provider.requests[0]
    assert request.provider_request.messages[0].text == original
    assert "command_name" not in request.metadata
    assert "skill_slash_command" not in request.metadata


@pytest.mark.asyncio
async def test_skill_slash_fallback_activates_discovered_skill(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/review-pr check the diff", session_id="session-skill-slash")

    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "system"
    assert "<available_skills>" in request.provider_request.messages[0].text
    assert '<skill_content name="review-pr">' in request.provider_request.messages[1].text
    assert request.provider_request.messages[2].role == "user"
    assert request.provider_request.messages[2].text == "check the diff"
    assert request.metadata["active_skills"] == ["review-pr"]
    assert request.metadata["skill_command"] == {
        "add": ["review-pr"],
        "clear": False,
        "cleaned_text": "check the diff",
    }
    assert request.metadata["skill_slash_command"] == "review-pr"
    assert request.metadata["skill_slash_arguments"] == "check the diff"
    assert "command_name" not in request.metadata


@pytest.mark.asyncio
async def test_skill_slash_fallback_handles_multiline_form(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run(
        "/review-pr\nPlease inspect this diff",
        session_id="session-skill-slash-multiline",
    )

    request = provider.requests[0]
    assert request.provider_request.messages[2].text == "Please inspect this diff"
    assert request.metadata["skill_command"]["cleaned_text"] == "Please inspect this diff"
    assert request.metadata["skill_slash_command"] == "review-pr"
    assert request.metadata["skill_slash_arguments"] == ""


@pytest.mark.asyncio
async def test_custom_command_wins_over_same_named_skill(tmp_path: Path):
    command_dir = _write_command(tmp_path, "review-pr.md", "# Review\nUse command.")
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/review-pr target", session_id="session-custom-before-skill")

    request = provider.requests[0]
    assert request.metadata["command_name"] == "review-pr"
    assert request.metadata["command_source"] == "file"
    assert request.metadata["command_arguments"] == "target"
    assert "skill_slash_command" not in request.metadata
    assert request.metadata["active_skills"] == []
    assert "<available_skills>" in request.provider_request.messages[0].text
    assert '<command name="review-pr"' in request.provider_request.messages[1].text


@pytest.mark.asyncio
async def test_skill_command_does_not_trigger_custom_command(tmp_path: Path):
    command_dir = _write_command(tmp_path, "skill.md", "# Custom Skill\nDo not use.")
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run(
        "/skill review-pr\nPlease inspect the diff.",
        session_id="session-skill-command",
    )

    request = provider.requests[0]
    assert runtime.active_skills == ["review-pr"]
    assert request.provider_request.messages[0].role == "system"
    assert "<available_skills>" in request.provider_request.messages[0].text
    assert '<skill_content name="review-pr">' in request.provider_request.messages[1].text
    assert request.provider_request.messages[2].role == "user"
    assert request.provider_request.messages[2].text == "Please inspect the diff."
    assert "command_name" not in request.metadata


@pytest.mark.asyncio
async def test_command_content_is_truncated_by_configured_limit(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "abcdef")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_command_chars=3,
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/fix", session_id="session-truncated-command")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert "<command" in text
    assert "\nabc\n</command>" in text
    assert "abcdef" not in text
    assert "<command_truncated" in text
    assert request.metadata["command_truncated"] is True
    assert request.metadata["command_original_chars"] == 6
    assert request.metadata["command_max_chars"] == 3


@pytest.mark.asyncio
async def test_command_shell_interpolation_renders_tool_results(tmp_path: Path):
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="inspect",
                content=(
                    "Before\n"
                    "!printf line-command\n"
                    "Between !`printf inline-command`\n"
                    "After"
                ),
                source="config",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            tool_permissions={"shell_exec": "allow"},
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run("/inspect", session_id="session-shell-command")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert text.count("<command_shell_result ") == 2
    assert 'status="success"' in text
    assert "line-command" in text
    assert "inline-command" in text
    assert "!printf line-command" not in text
    assert "!`printf inline-command`" not in text
    assert request.metadata["command_shell_interpolation_count"] == 2
    assert [
        interpolation["status"]
        for interpolation in request.metadata["command_shell_interpolations"]
    ] == ["success", "success"]
    assert [
        interpolation["tool_id"]
        for interpolation in request.metadata["command_shell_interpolations"]
    ] == ["shell_exec", "shell_exec"]


@pytest.mark.asyncio
async def test_command_shell_interpolation_permission_denial_is_visible(
    tmp_path: Path,
):
    marker = tmp_path / "marker"
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="danger",
                content=f"!touch {marker}",
                source="config",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            tool_permissions={"shell_exec": "deny"},
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run("/danger", session_id="session-shell-command-denied")

    text = provider.requests[0].provider_request.messages[0].text
    assert not marker.exists()
    assert "<command_shell_result " in text
    assert 'status="permission_denied"' in text
    assert "Permission denied" in text


@pytest.mark.asyncio
async def test_command_arguments_and_body_shell_syntax_are_not_interpolated(
    tmp_path: Path,
):
    marker = tmp_path / "marker"
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="inspect",
                content="Inspect the supplied text.",
                source="config",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            tool_permissions={"shell_exec": "allow"},
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run(
        f"/inspect !touch {marker}\n!touch {marker}",
        session_id="session-shell-command-scope",
    )

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert not marker.exists()
    assert f"<command_arguments>\n!touch {marker}\n</command_arguments>" in text
    assert f"<command_input>\n!touch {marker}\n</command_input>" in text
    assert "<command_shell_result " not in text
    assert request.metadata["command_shell_interpolation_count"] == 0
    assert request.metadata["command_shell_interpolations"] == []


@pytest.mark.asyncio
async def test_command_file_references_are_resolved_after_expansion(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("alpha\n", encoding="utf-8")
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="inspect",
                content="Inspect @notes.txt",
                source="config",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    await runtime.run("/inspect", session_id="session-command-reference")

    message = provider.requests[0].provider_request.messages[0]
    assert "@notes.txt" in message.parts[0].text
    assert message.attachments[0].text_ref == "notes.txt"
    assert message.attachments[0].metadata["content"] == "alpha\n"


def test_runtime_config_resolution_preserves_command_fields(tmp_path: Path):
    command_dir = tmp_path / "commands"
    base = RuntimeConfig(
        command_directories=[command_dir],
        enable_command_expansion=False,
        max_command_chars=123,
        metadata={"base": "yes"},
    )

    resolved = _resolve_config(
        base,
        workspace_root=None,
        max_iterations=None,
        max_context_parts=None,
        max_context_chars=None,
        context_reserve_chars=None,
        metadata={"run": "yes"},
    )
    child = _child_config(
        profile=AgentProfile(name="reviewer"),
        base_config=base,
        workspace_root=None,
        metadata={"task": "yes"},
    )

    assert resolved.command_directories == [command_dir]
    assert resolved.enable_command_expansion is False
    assert resolved.max_command_chars == 123
    assert resolved.metadata == {"base": "yes", "run": "yes"}
    assert child.command_directories == [command_dir]
    assert child.enable_command_expansion is False
    assert child.max_command_chars == 123
    assert child.metadata == {"base": "yes", "task": "yes"}


def test_custom_command_imports_do_not_cross_runtime_v2_boundary():
    code = """
import json
import sys

import efp_runtime.commands
import efp_runtime.runtime

blocked = [
    "src.agents.core",
    "src.agents.skill_runtime",
    "src.agents.skill_mode",
    "src.runtime",
    "src.sessions",
    "src.skills",
    "src.skills.runtime",
]
print(json.dumps([name for name in blocked if name in sys.modules]))
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

    assert json.loads(result.stdout.strip().splitlines()[-1]) == []

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
    )
    for token in [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.skills",
        "import src.skills",
    ]:
        assert token not in combined


def _write_command(tmp_path: Path, filename: str, content: str) -> Path:
    command_dir = tmp_path / "commands"
    command_dir.mkdir(exist_ok=True)
    (command_dir / filename).write_text(content, encoding="utf-8")
    return command_dir


def _last_user_text(request: Any) -> str:
    for message in reversed(request.provider_request.messages):
        if message.role == "user":
            return message.text
    raise AssertionError("provider request did not contain a user message")


def _write_skill(
    tmp_path: Path,
    name: str,
    *,
    description: str = "Loads skill context",
    content: str = "# Skill\nUse this context.",
) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir


def _tool(
    tool_id: str,
    *,
    execute=None,
) -> ToolDef:
    async def default_execute(args: dict[str, Any], context):
        return {"tool_id": tool_id, "args": args, "session_id": context.session_id}

    return ToolDef(
        id=tool_id,
        description=f"{tool_id} tool",
        input_schema={"type": "object", "properties": {}},
        execute=execute or default_execute,
    )
