from __future__ import annotations

from dataclasses import asdict
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
    command_template_hints,
    expand_command,
)
from efp_runtime.config_loader import load_runtime_config
from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.llm.provider import OpenAICompatibleProvider, RecordingTransport
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.runtime.agent import _resolve_config
from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.builtin.task import (
    TaskToolRequest,
    TaskToolResult,
    create_task_tool,
)
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


def test_command_registry_list_returns_safe_effective_command_info(tmp_path: Path):
    definitions = [
        *builtin_command_definitions(tmp_path),
        *command_definitions_from_config(
            {
                "command": {
                    "review": {
                        "template": "Config review $1",
                        "description": "Config review",
                        "argument-hint": "<config-target>",
                        "agent": "config-agent",
                        "model": "provider/config",
                        "tools": ["edit"],
                        "subtask": False,
                    },
                    "deploy": {
                        "template": "Deploy $1 with $ARGUMENTS",
                        "description": "Deploy service",
                        "argument-hint": "<service>",
                        "agent": "release",
                        "model": "provider/release",
                        "tools": "edit, shell_exec, edit, ",
                        "subtask": {"kind": "release"},
                    },
                    "maptools": {
                        "template": "Mapped tools",
                        "tools": {"read_file": True},
                    },
                }
            }
        ),
    ]
    config_registry = CommandRegistry.from_sources(definitions=definitions)

    config_infos = {info.name: info for info in config_registry.list()}

    assert config_infos["init"].source == "builtin"
    assert config_infos["init"].command_file is None
    assert config_infos["review"].source == "config"
    assert config_infos["review"].description == "Config review"
    assert config_infos["review"].command_file is None

    command_dir = tmp_path / ".opencode" / "commands"
    command_dir.mkdir(parents=True)
    command_file = command_dir / "review.md"
    command_file.write_text(
        "---\n"
        "description: File review\n"
        "argument-hint: <file-target>\n"
        "agent: file-agent\n"
        "model: provider/file\n"
        "subtask: true\n"
        "tools: [read_file, shell_exec, read_file]\n"
        "---\n"
        "Run $2 then $1 with $ARGUMENTS and $HOME\n",
        encoding="utf-8",
    )
    registry = CommandRegistry.from_sources(
        definitions=definitions,
        command_directories=[command_dir],
    )

    infos = {info.name: info for info in registry.list()}

    assert sorted(infos) == ["deploy", "init", "maptools", "review"]
    review = infos["review"]
    assert review.description == "File review"
    assert review.source == "file"
    assert review.argument_hint == "<file-target>"
    assert review.agent == "file-agent"
    assert review.model == "provider/file"
    assert review.subtask is True
    assert review.tools == ["read_file", "shell_exec"]
    assert review.hints == ["$1", "$2", "$ARGUMENTS"]
    assert review.command_file == command_file
    assert review.metadata["source"] == "file"
    assert review.metadata["tools"] == ["read_file", "shell_exec", "read_file"]
    assert not hasattr(review, "content")
    assert "content" not in asdict(review)

    assert infos["deploy"].source == "config"
    assert infos["deploy"].command_file is None
    assert infos["deploy"].argument_hint == "<service>"
    assert infos["deploy"].agent == "release"
    assert infos["deploy"].model == "provider/release"
    assert infos["deploy"].subtask == {"kind": "release"}
    assert infos["deploy"].tools == ["edit", "shell_exec"]
    assert infos["deploy"].hints == ["$1", "$ARGUMENTS"]
    assert infos["maptools"].tools == []

    review.metadata["tools"].append("write_file")
    review.tools.append("write_file")
    review.hints.append("$99")
    fresh_info = {info.name: info for info in registry.list()}["review"]
    assert fresh_info.metadata["tools"] == ["read_file", "shell_exec", "read_file"]
    assert fresh_info.tools == ["read_file", "shell_exec"]
    assert fresh_info.hints == ["$1", "$2", "$ARGUMENTS"]


def test_command_registry_exposes_discovered_skill_as_command(tmp_path: Path):
    skill_dir = _write_skill(
        tmp_path,
        "reviewer",
        description="Review changes",
        content="# Reviewer\nInspect $ARGUMENTS.",
    )
    registry = CommandRegistry.from_sources(
        skill_discovery=SkillDiscovery([tmp_path]),
    )

    command = registry.get("reviewer")
    info = {item.name: item for item in registry.list()}["reviewer"]
    expansion = expand_command("/reviewer inspect this", registry)

    assert command is not None
    assert command.name == "reviewer"
    assert command.description == "Review changes"
    assert command.content == "# Reviewer\nInspect $ARGUMENTS."
    assert command.source == "skill"
    assert command.command_file == skill_dir / "SKILL.md"
    assert command.metadata["name"] == "reviewer"
    assert command.metadata["source"] == "skill"
    assert command.metadata["skill_name"] == "reviewer"
    assert command.metadata["skill_file"] == str(skill_dir / "SKILL.md")
    assert command.metadata["skill_root"] == str(skill_dir)
    assert info.source == "skill"
    assert info.command_file == skill_dir / "SKILL.md"
    assert not hasattr(info, "content")
    assert "content" not in asdict(info)
    assert expansion is not None
    assert expansion.definition.source == "skill"
    assert "# Reviewer\nInspect inspect this." in expansion.text
    assert "<command_arguments>\ninspect this\n</command_arguments>" in expansion.text


def test_skill_commands_do_not_override_existing_commands(tmp_path: Path):
    command_dir = _write_command(tmp_path, "auditor.md", "File auditor.")
    _write_skill(tmp_path, "reviewer", content="Skill reviewer.")
    _write_skill(tmp_path, "auditor", content="Skill auditor.")
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="reviewer",
                content="Config reviewer.",
                source="config",
            )
        ],
        command_directories=[command_dir],
        skill_discovery=SkillDiscovery([tmp_path]),
    )

    commands = {command.name: command for command in registry.discover()}
    infos = {info.name: info for info in registry.list()}

    assert commands["reviewer"].source == "config"
    assert commands["reviewer"].content == "Config reviewer."
    assert commands["auditor"].source == "file"
    assert commands["auditor"].content == "File auditor."
    assert infos["reviewer"].source == "config"
    assert infos["auditor"].source == "file"


def test_command_template_hints_ignore_environment_variables():
    assert command_template_hints(
        "Run $2 then $1 with $ARGUMENTS and $HOME"
    ) == ["$1", "$2", "$ARGUMENTS"]
    assert command_template_hints(
        "$1 $1 ${VAR} $FOO $10 $2x $ARGUMENTS_EXTRA"
    ) == ["$1", "$10"]


def test_command_registry_list_refresh_controls_file_cache(tmp_path: Path):
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    alpha = command_dir / "alpha.md"
    alpha.write_text("description: Alpha\n\nRun $1\n", encoding="utf-8")
    registry = CommandRegistry([command_dir])

    initial = {info.name: info for info in registry.list()}

    assert sorted(initial) == ["alpha"]
    assert initial["alpha"].description == "Alpha"
    assert initial["alpha"].hints == ["$1"]

    alpha.write_text(
        "description: Alpha updated\nagent: cache\n\nRun $2\n",
        encoding="utf-8",
    )
    beta = command_dir / "beta.md"
    beta.write_text("description: Beta\n\nUse $ARGUMENTS\n", encoding="utf-8")

    cached = {info.name: info for info in registry.list()}

    assert sorted(cached) == ["alpha"]
    assert cached["alpha"].description == "Alpha"
    assert cached["alpha"].agent is None
    assert cached["alpha"].hints == ["$1"]

    refreshed = {info.name: info for info in registry.list(refresh=True)}

    assert sorted(refreshed) == ["alpha", "beta"]
    assert refreshed["alpha"].description == "Alpha updated"
    assert refreshed["alpha"].agent == "cache"
    assert refreshed["alpha"].hints == ["$2"]
    assert refreshed["beta"].description == "Beta"
    assert refreshed["beta"].hints == ["$ARGUMENTS"]


def test_command_registry_list_refresh_controls_skill_cache(tmp_path: Path):
    _write_skill(tmp_path, "alpha-skill", description="Alpha")
    registry = CommandRegistry.from_sources(
        skill_discovery=SkillDiscovery([tmp_path]),
    )

    initial = {info.name: info for info in registry.list()}
    _write_skill(tmp_path, "beta-skill", description="Beta")
    cached = {info.name: info for info in registry.list()}
    refreshed = {info.name: info for info in registry.list(refresh=True)}

    assert sorted(initial) == ["alpha-skill"]
    assert initial["alpha-skill"].source == "skill"
    assert sorted(cached) == ["alpha-skill"]
    assert sorted(refreshed) == ["alpha-skill", "beta-skill"]
    assert refreshed["beta-skill"].source == "skill"
    assert refreshed["beta-skill"].description == "Beta"


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
            include_environment_context=False,
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
async def test_skill_backed_slash_command_expands_into_provider_user_message(
    tmp_path: Path,
):
    skill_dir = _write_skill(
        tmp_path,
        "review-pr",
        description="Review pull requests",
        content="# Review\nInspect $ARGUMENTS.",
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    assert runtime.command_registry.get("review-pr").source == "skill"

    await runtime.run("/review-pr check the diff", session_id="session-skill-command")

    request = provider.requests[0]
    text = _last_user_text(request)
    assert '<command name="review-pr"' in text
    assert f'source="{skill_dir / "SKILL.md"}"' in text
    assert 'command_source="skill"' in text
    assert "# Review\nInspect check the diff." in text
    assert "<command_arguments>\ncheck the diff\n</command_arguments>" in text
    assert request.metadata["command_name"] == "review-pr"
    assert request.metadata["command_source"] == "skill"
    assert request.metadata["command_file"] == str(skill_dir / "SKILL.md")
    assert request.metadata["command_arguments"] == "check the diff"
    assert request.metadata["command_metadata"]["skill_name"] == "review-pr"
    assert request.metadata["command_metadata"]["skill_file"] == str(
        skill_dir / "SKILL.md"
    )
    assert request.metadata["active_skills"] == []
    assert "skill_slash_command" not in request.metadata
    assert all(
        '<skill_content name="review-pr">' not in message.text
        for message in request.provider_request.messages
    )


@pytest.mark.asyncio
async def test_run_command_matches_slash_invocation_prompt_and_metadata(
    tmp_path: Path,
):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nUse $ARGUMENTS.")
    arguments = 'ticket-1 --flag "two words"'
    config = RuntimeConfig(
        command_directories=[command_dir],
        max_iterations=1,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=False,
    )
    slash_provider = ScriptedLLMProvider([{"content": "Done."}])
    slash_runtime = AgentRuntime(provider=slash_provider, config=config)
    direct_provider = ScriptedLLMProvider([{"content": "Done."}])
    direct_runtime = AgentRuntime(provider=direct_provider, config=config)

    await slash_runtime.run(
        f"/fix {arguments}\nextra",
        session_id="session-slash-command",
    )
    await direct_runtime.run_command(
        "fix",
        arguments=arguments,
        input_text="extra",
        session_id="session-direct-command",
    )

    slash_request = slash_provider.requests[0]
    direct_request = direct_provider.requests[0]
    slash_text = _last_user_text(slash_request)
    direct_text = _last_user_text(direct_request)
    assert direct_text == slash_text
    assert f"<command_arguments>\n{arguments}\n</command_arguments>" in direct_text
    assert "<command_input>\nextra\n</command_input>" in direct_text
    for key in [
        "command_name",
        "command_file",
        "command_arguments",
        "command_source",
        "command_metadata",
        "command_truncated",
        "command_original_chars",
        "command_max_chars",
    ]:
        assert direct_request.metadata[key] == slash_request.metadata[key]
    assert direct_request.metadata["command_invocation"] == "direct"
    assert direct_request.provider_request.metadata["command_invocation"] == "direct"
    assert "command_invocation" not in slash_request.metadata


@pytest.mark.asyncio
async def test_run_command_accepts_leading_slash_in_command_name(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nApply this fix.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run_command("/fix", arguments="ticket-123")

    request = provider.requests[0]
    assert request.metadata["command_name"] == "fix"
    assert request.metadata["command_arguments"] == "ticket-123"
    assert request.metadata["command_invocation"] == "direct"


@pytest.mark.asyncio
async def test_run_command_unknown_command_lists_available_names():
    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(name="fix", content="# Fix"),
            CommandDefinition(name="review", content="# Review"),
        ]
    )
    provider = ScriptedLLMProvider([{"content": "unused"}])
    runtime = AgentRuntime(provider=provider, command_registry=registry)

    with pytest.raises(ValueError) as excinfo:
        await runtime.run_command("missing")

    message = str(excinfo.value)
    assert "unknown command 'missing'" in message
    assert "fix" in message
    assert "review" in message
    assert provider.requests == []


@pytest.mark.asyncio
async def test_run_command_rejects_skill_command():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    runtime = AgentRuntime(provider=provider)

    with pytest.raises(ValueError, match="skill"):
        await runtime.run_command("skill")

    assert provider.requests == []


@pytest.mark.asyncio
async def test_run_command_invokes_skill_backed_command(tmp_path: Path):
    skill_dir = _write_skill(
        tmp_path,
        "review-pr",
        description="Review pull requests",
        content="# Review\nInspect $ARGUMENTS.",
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    assert runtime.command_registry.get("review-pr").source == "skill"

    await runtime.run_command(
        "review-pr",
        arguments="check the diff",
        session_id="session-direct-skill-command",
    )

    request = provider.requests[0]
    text = _last_user_text(request)
    assert '<command name="review-pr"' in text
    assert f'source="{skill_dir / "SKILL.md"}"' in text
    assert 'command_source="skill"' in text
    assert "# Review\nInspect check the diff." in text
    assert "<command_arguments>\ncheck the diff\n</command_arguments>" in text
    assert request.metadata["command_name"] == "review-pr"
    assert request.metadata["command_source"] == "skill"
    assert request.metadata["command_arguments"] == "check the diff"
    assert request.metadata["command_invocation"] == "direct"
    assert request.metadata["command_metadata"]["skill_name"] == "review-pr"
    assert request.metadata["command_metadata"]["skill_file"] == str(
        skill_dir / "SKILL.md"
    )
    assert request.metadata["active_skills"] == []
    assert "skill_slash_command" not in request.metadata


@pytest.mark.asyncio
async def test_run_command_emits_command_executed_event_once(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nApply this fix.")
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        event_bus=bus,
    )

    result = await runtime.run_command(
        "fix",
        arguments="ticket-123",
        session_id="session-direct-command-event",
    )

    command_events = [
        event
        for event in bus.history("session-direct-command-event")
        if event.type == "command.executed"
    ]
    result_command_events = [
        event for event in result.runtime_events if event.type == "command.executed"
    ]
    assert len(command_events) == 1
    assert result_command_events == command_events
    assert command_events[0].payload["name"] == "fix"
    assert command_events[0].payload["arguments"] == "ticket-123"
    assert provider.requests[0].metadata["command_invocation"] == "direct"


@pytest.mark.asyncio
async def test_slash_command_emits_command_executed_event(tmp_path: Path):
    command_body = "# Fix\nApply this fix."
    command_dir = _write_command(
        tmp_path,
        "fix.md",
        "---\ndescription: Fix defects\n---\n" + command_body,
    )
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        event_bus=bus,
    )

    result = await runtime.run("/fix ticket-123", session_id="session-command-event")

    command_events = [
        event
        for event in bus.history("session-command-event")
        if event.type == "command.executed"
    ]
    assert len(command_events) == 1
    assert [
        event for event in result.runtime_events if event.type == "command.executed"
    ] == command_events

    event = command_events[0]
    assert event.message == "Command executed."
    assert event.session_id == "session-command-event"
    assert event.message_id == result.final_assistant_message.message_id
    assert event.payload == {
        "name": "fix",
        "arguments": "ticket-123",
        "source": "file",
        "status": LoopStatus.COMPLETED,
        "run_id": provider.requests[0].metadata["run_id"],
        "command_metadata": {
            "description": "Fix defects",
            "source": "file",
            "name": "fix",
        },
        "truncated": False,
        "original_chars": len(command_body),
        "max_chars": 20000,
    }


@pytest.mark.asyncio
async def test_command_executed_event_emits_on_pause_and_not_resume(tmp_path: Path):
    command_dir = _write_command(tmp_path, "fix.md", "# Fix\nWrite the file.")
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "created.txt",
                                    "content": "approved\n",
                                }
                            ),
                        },
                    }
                ]
            },
            {"content": "File written."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            command_directories=[command_dir],
            max_iterations=3,
            include_legacy_tool_aliases=True,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        event_bus=bus,
    )

    first = await runtime.run("/fix ticket-123", session_id="session-command-pause")

    assert first.status == LoopStatus.WAITING_FOR_PERMISSION
    command_events = [
        event
        for event in bus.history("session-command-pause")
        if event.type == "command.executed"
    ]
    assert len(command_events) == 1
    assert command_events[0].payload["status"] == LoopStatus.WAITING_FOR_PERMISSION
    assert command_events[0].message_id == first.final_assistant_message.message_id

    runtime.approve_permission(first.pending_permission_request["request_id"])
    resumed = await runtime.resume("session-command-pause")

    assert resumed.status == LoopStatus.COMPLETED
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "approved\n"
    assert [
        event
        for event in bus.history("session-command-pause")
        if event.type == "command.executed"
    ] == command_events

@pytest.mark.asyncio
async def test_default_command_file_available_when_loaded_from_nested_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    command_dir = project / ".opencode" / "commands"
    command_dir.mkdir(parents=True)
    command_file = command_dir / "audit.md"
    command_file.write_text("# Audit\nInspect project context.", encoding="utf-8")
    loaded = load_runtime_config(nested)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=loaded.config,
        command_registry=loaded.command_registry,
    )

    await runtime.run("/audit src/core", session_id="session-nested-command")

    request = provider.requests[0]
    text = _last_user_text(request)
    assert loaded.config.workspace_root == project.resolve()
    assert loaded.config.command_directories == [command_dir.resolve()]
    assert request.metadata["command_name"] == "audit"
    assert request.metadata["command_source"] == "file"
    assert request.metadata["command_file"] == str(command_file.resolve())
    assert request.metadata["command_arguments"] == "src/core"
    assert "# Audit\nInspect project context." in text


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
            include_environment_context=False,
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
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/review feature/rework", session_id="session-builtin-review")

    request = provider.requests[0]
    text = request.provider_request.messages[0].text
    assert request.metadata["command_name"] == "review"
    assert request.metadata["command_source"] == "builtin"
    assert request.metadata["command_subtask"] is True
    assert request.metadata["command_subtask_requested"] is True
    assert request.metadata["command_subtask_available"] is False
    assert request.metadata["command_subtask_executed"] is False
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
            include_environment_context=False,
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
async def test_command_subtask_true_executes_task_tool_before_parent_provider():
    captured: list[TaskToolRequest] = []

    async def task_runner(request: TaskToolRequest) -> TaskToolResult:
        captured.append(request)
        return TaskToolResult(
            task_id=request.task_id,
            text="child review result",
            metadata={"child": "metadata"},
        )

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="review",
                description="Review changes",
                content="Review expanded $ARGUMENTS.",
                source="config",
                subtask=True,
            )
        ]
    )
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Parent final."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
        event_bus=bus,
    )

    result = await runtime.run(
        "/review src/runtime\nKeep this parent context.",
        session_id="session-command-subtask",
    )

    assert len(captured) == 1
    task_request = captured[0]
    assert task_request.description == "Review changes"
    assert task_request.prompt == "Review expanded src/runtime."
    assert task_request.subagent_type == "general"
    assert task_request.task_id.startswith("command-task_")
    assert task_request.command == "review"
    assert task_request.session_id == "session-command-subtask"
    assert task_request.metadata["session_id"] == "session-command-subtask"
    assert task_request.metadata["run_id"] == provider.requests[0].metadata["run_id"]
    assert task_request.metadata["command_name"] == "review"

    parent_text = _last_user_text(provider.requests[0])
    assert '<command_subtask_result name="review" agent="general"' in parent_text
    assert f'task_id="{task_request.task_id}"' in parent_text
    assert "child review result" in parent_text
    assert "Keep this parent context." in parent_text
    assert '<command name="review"' not in parent_text
    assert "Review expanded src/runtime." not in parent_text

    metadata = provider.requests[0].metadata
    assert metadata["command_subtask_executed"] is True
    assert metadata["command_subtask_requested"] is True
    assert metadata["command_subtask_available"] is True
    assert metadata["command_subtask_subagent_type"] == "general"
    assert metadata["command_subtask_task_id"] == task_request.task_id
    assert metadata["command_subtask_result_status"] == "success"
    assert metadata["command_subtask_output_metadata"] == {"child": "metadata"}
    assert metadata["command_name"] == "review"
    assert metadata["command_arguments"] == "src/runtime"
    assert metadata["command_metadata"]["subtask"] is True

    task_tool_events = [
        event
        for event in result.runtime_events
        if event.payload.get("source") == "command.subtask"
        and event.payload.get("tool_call_id") == task_request.task_id
    ]
    assert [event.type for event in task_tool_events] == [
        "tool.started",
        "tool.completed",
    ]
    for event in task_tool_events:
        assert event.session_id == "session-command-subtask"
        assert event.payload["source"] == "command.subtask"
        assert event.payload["command_name"] == "review"
        assert event.payload["task_id"] == task_request.task_id
        assert event.payload["subagent_type"] == "general"
        assert event.payload["tool_id"] == "task"
        assert event.payload["tool_name"] == "task"
        assert event.payload["tool_call_id"] == task_request.task_id
        assert event.payload["run_id"] == metadata["run_id"]

    bus_task_tool_events = [
        event
        for event in bus.history("session-command-subtask")
        if event.payload.get("source") == "command.subtask"
        and event.payload.get("tool_call_id") == task_request.task_id
    ]
    assert bus_task_tool_events == task_tool_events
    assert all(
        bus_event is result_event
        for bus_event, result_event in zip(bus_task_tool_events, task_tool_events)
    )

    subtask_events = [
        event for event in result.runtime_events if event.type == "command.subtask.completed"
    ]
    command_events = [
        event for event in result.runtime_events if event.type == "command.executed"
    ]
    assert len(subtask_events) == 1
    assert len(command_events) == 1
    assert result.runtime_events.index(task_tool_events[-1]) < result.runtime_events.index(
        subtask_events[0]
    )
    assert subtask_events[0].payload == {
        "run_id": metadata["run_id"],
        "command": "review",
        "task_id": task_request.task_id,
        "subagent_type": "general",
        "status": "success",
        "success": True,
    }


@pytest.mark.asyncio
async def test_command_agent_subagent_profile_executes_task_when_subtask_omitted():
    captured: list[TaskToolRequest] = []

    async def task_runner(request: TaskToolRequest) -> str:
        captured.append(request)
        return "reviewer child result"

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="audit",
                content="Audit $ARGUMENTS.",
                source="config",
                agent="reviewer",
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Parent final."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        agent_registry=AgentRegistry(
            [AgentProfile(name="reviewer", metadata={"mode": "subagent"})],
            default_agent=None,
        ),
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
    )

    await runtime.run("/audit target", session_id="session-command-profile-subtask")

    assert len(captured) == 1
    assert captured[0].prompt == "Audit target."
    assert captured[0].subagent_type == "reviewer"
    request = provider.requests[0]
    assert request.metadata["selected_agent_source"] == "command"
    assert request.metadata["command_subtask_requested"] is True
    assert request.metadata["command_subtask_executed"] is True
    assert request.metadata["command_subtask_subagent_type"] == "reviewer"
    assert "reviewer child result" in _last_user_text(request)
    assert '<command name="audit"' not in _last_user_text(request)


@pytest.mark.asyncio
async def test_command_subtask_false_overrides_subagent_profile_mode():
    called = False

    async def task_runner(request: TaskToolRequest) -> str:
        nonlocal called
        called = True
        return "should not run"

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="audit",
                content="Audit $ARGUMENTS.",
                source="config",
                agent="reviewer",
                subtask=False,
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Parent final."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        agent_registry=AgentRegistry(
            [AgentProfile(name="reviewer", metadata={"mode": "subagent"})],
            default_agent=None,
        ),
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
    )

    await runtime.run("/audit target", session_id="session-command-subtask-false")

    request = provider.requests[0]
    assert called is False
    assert request.metadata["command_subtask"] is False
    assert request.metadata["command_subtask_requested"] is False
    assert request.metadata["command_subtask_executed"] is False
    assert '<command name="audit"' in _last_user_text(request)
    assert "<command_subtask_result" not in _last_user_text(request)


@pytest.mark.asyncio
async def test_command_subtask_respects_disabled_task_tool():
    called = False

    async def task_runner(request: TaskToolRequest) -> str:
        nonlocal called
        called = True
        return "should not run"

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="review",
                content="Review $ARGUMENTS.",
                source="config",
                subtask=True,
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Parent fallback."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            disabled_tools=["task"],
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
    )

    await runtime.run("/review branch", session_id="session-command-subtask-disabled")

    request = provider.requests[0]
    assert called is False
    assert request.metadata["command_subtask_requested"] is True
    assert request.metadata["command_subtask_available"] is False
    assert request.metadata["command_subtask_executed"] is False
    text = _last_user_text(request)
    assert '<command name="review"' in text
    assert "Review branch." in text
    assert "<command_subtask_result" not in text


@pytest.mark.asyncio
async def test_command_subtask_respects_per_run_task_disable():
    called = False

    async def task_runner(request: TaskToolRequest) -> str:
        nonlocal called
        called = True
        return "should not run"

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="review",
                content="Review $ARGUMENTS.",
                source="config",
                subtask=True,
            )
        ]
    )
    provider = ScriptedLLMProvider([{"content": "Parent fallback."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
    )

    await runtime.run(
        "/review branch",
        session_id="session-command-subtask-run-disabled",
        tools={"task": False},
    )

    request = provider.requests[0]
    assert called is False
    assert request.metadata["command_subtask_requested"] is True
    assert request.metadata["command_subtask_available"] is False
    assert request.metadata["command_subtask_executed"] is False
    text = _last_user_text(request)
    assert '<command name="review"' in text
    assert "Review branch." in text
    assert "<command_subtask_result" not in text


@pytest.mark.asyncio
async def test_command_subtask_error_falls_back_to_ordinary_command_prompt():
    captured: list[TaskToolRequest] = []

    async def task_runner(request: TaskToolRequest) -> TaskToolResult:
        captured.append(request)
        return TaskToolResult(
            task_id=request.task_id,
            text="child failed",
            state="error",
            metadata={"phase": "child"},
        )

    registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="review",
                content="Review $ARGUMENTS.",
                source="config",
                subtask=True,
            )
        ]
    )
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Parent fallback."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
        tool_registry=ToolRegistry([create_task_tool(task_runner)]),
        event_bus=bus,
    )

    result = await runtime.run(
        "/review branch",
        session_id="session-command-subtask-error",
    )

    assert len(captured) == 1
    request = provider.requests[0]
    text = _last_user_text(request)
    assert '<command name="review"' in text
    assert "<command_subtask_result" not in text
    assert "Review branch." in text
    assert request.metadata["command_subtask_requested"] is True
    assert request.metadata["command_subtask_available"] is True
    assert request.metadata["command_subtask_executed"] is False
    assert request.metadata["command_subtask_result_status"] == "error"
    assert request.metadata["command_subtask_result_error"] == "child failed"
    assert request.metadata["command_subtask_output_metadata"] == {"phase": "child"}
    task_id = request.metadata["command_subtask_task_id"]
    task_tool_events = [
        event
        for event in result.runtime_events
        if event.payload.get("source") == "command.subtask"
        and event.payload.get("tool_call_id") == task_id
    ]
    assert [event.type for event in task_tool_events] == [
        "tool.started",
        "tool.completed",
    ]
    for event in task_tool_events:
        assert event.session_id == "session-command-subtask-error"
        assert event.payload["source"] == "command.subtask"
        assert event.payload["command_name"] == "review"
        assert event.payload["task_id"] == task_id
        assert event.payload["subagent_type"] == "general"
        assert event.payload["tool_id"] == "task"
        assert event.payload["tool_name"] == "task"
        assert event.payload["tool_call_id"] == task_id
        assert event.payload["run_id"] == request.metadata["run_id"]
    assert task_tool_events[-1].payload["status"] == "error"
    assert task_tool_events[-1].payload["success"] is False
    bus_task_tool_events = [
        event
        for event in bus.history("session-command-subtask-error")
        if event.payload.get("source") == "command.subtask"
        and event.payload.get("tool_call_id") == task_id
    ]
    assert bus_task_tool_events == task_tool_events
    assert all(
        bus_event is result_event
        for bus_event, result_event in zip(bus_task_tool_events, task_tool_events)
    )
    assert not [
        event for event in result.runtime_events if event.type == "command.subtask.completed"
    ]


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
            include_environment_context=False,
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
            include_environment_context=False,
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
            include_environment_context=False,
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
            include_environment_context=False,
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
            include_environment_context=False,
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
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        event_bus=bus,
    )

    original = "/missing foo\nkeep this body"
    await runtime.run(original, session_id="session-unknown-command")

    request = provider.requests[0]
    assert request.provider_request.messages[0].text == original
    assert "command_name" not in request.metadata
    assert "skill_slash_command" not in request.metadata
    assert not [
        event
        for event in bus.history("session-unknown-command")
        if event.type == "command.executed"
    ]


@pytest.mark.asyncio
async def test_skill_slash_fallback_activates_discovered_skill_when_unhandled(
    tmp_path: Path,
):
    _write_skill(tmp_path, "review-pr")
    registry = CommandRegistry.from_sources(definitions=[])
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
    )

    assert runtime.command_registry is registry
    assert runtime.command_registry.get("review-pr") is None

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
    registry = CommandRegistry.from_sources(definitions=[])
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        command_registry=registry,
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
            include_environment_context=False,
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
    bus = RuntimeEventBus()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            command_directories=[command_dir],
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        event_bus=bus,
    )

    assert runtime.command_registry.get("review-pr").source == "skill"

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
    assert not [
        event
        for event in bus.history("session-skill-command")
        if event.type == "command.executed"
    ]


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
            include_environment_context=False,
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
            include_legacy_tool_aliases=True,
            tool_permissions={"shell_exec": "allow"},
            include_default_system_prompt=False,
            include_environment_context=False,
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
            include_legacy_tool_aliases=True,
            tool_permissions={"shell_exec": "deny"},
            include_default_system_prompt=False,
            include_environment_context=False,
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
            include_environment_context=False,
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
            include_environment_context=False,
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
