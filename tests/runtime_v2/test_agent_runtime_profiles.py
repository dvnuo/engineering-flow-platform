from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.agents import AgentProfile, AgentRegistry
from efp_runtime.commands import CommandDefinition, CommandRegistry
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.models import MessageRole
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_named_agent_injects_profile_prompt_into_provider_system_context():
    provider = ScriptedLLMProvider([{"content": "Reviewed."}])
    registry = AgentRegistry(
        [
            AgentProfile(
                name="review",
                description="Reviews code",
                prompt="Use the review profile.",
            )
        ],
        default_agent=None,
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
            system_prompt_texts=["Base system prompt."],
            instruction_texts=["Workspace instructions."],
            include_default_instructions=False,
        ),
        agent_registry=registry,
    )

    result = await runtime.run("Review this change.", session_id="session-review", agent="review")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    messages = request.provider_request.messages
    assert [message.role for message in messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    assert messages[0].text == "Base system prompt."
    assert messages[1].text == "Use the review profile."
    assert messages[2].text == "Workspace instructions."
    assert messages[3].text == "Review this change."
    assert request.metadata["selected_agent_source"] == "caller"
    assert request.metadata["agent_name"] == "review"
    assert request.metadata["agent_description"] == "Reviews code"
    assert request.metadata["agent_prompt_context_count"] == 1


@pytest.mark.asyncio
async def test_profile_prompt_is_not_written_to_session_history():
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )
    profile = AgentProfile(name="review", prompt="Transient profile prompt.")

    await runtime.run("Keep history clean.", session_id="session-history", agent=profile)

    history = runtime.store.read_history("session-history")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all("Transient profile prompt." not in _message_text(message) for message in history)


@pytest.mark.asyncio
async def test_default_agent_is_used_when_run_omits_agent():
    provider = ScriptedLLMProvider([{"content": "Defaulted."}])
    registry = AgentRegistry(
        [AgentProfile(name="review", prompt="Default review profile.")],
        default_agent=None,
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        agent_registry=registry,
        default_agent="review",
    )

    await runtime.run("Use default.", session_id="session-default")

    request = provider.requests[0]
    assert request.metadata["selected_agent_source"] == "default"
    assert request.metadata["agent_name"] == "review"
    assert request.provider_request.messages[0].text == "Default review profile."


@pytest.mark.asyncio
async def test_direct_agent_profile_does_not_require_registry():
    provider = ScriptedLLMProvider([{"content": "Direct."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run(
        "Use direct profile.",
        session_id="session-direct",
        agent=AgentProfile(name="direct", prompt="Direct profile prompt."),
    )

    request = provider.requests[0]
    assert request.metadata["agent_name"] == "direct"
    assert request.provider_request.messages[0].text == "Direct profile prompt."


@pytest.mark.asyncio
async def test_profile_active_skills_are_base_and_skill_commands_are_temporary(
    tmp_path: Path,
):
    _write_skill(tmp_path, "runtime-skill")
    _write_skill(tmp_path, "profile-skill")
    _write_skill(tmp_path, "extra-skill")
    provider = ScriptedLLMProvider(
        [
            {"content": "Added."},
            {"content": "Cleared."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            active_skills=["runtime-skill"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )
    profile = AgentProfile(name="review", active_skills=["profile-skill"])

    await runtime.run(
        "/skill extra-skill\nUse profile skills.",
        session_id="session-profile-skill-add",
        agent=profile,
    )
    await runtime.run(
        "/skill clear\nUse no profile skills.",
        session_id="session-profile-skill-clear",
        agent=profile,
    )

    assert runtime.active_skills == ["runtime-skill"]
    first_request = provider.requests[0]
    assert first_request.metadata["active_skills"] == [
        "profile-skill",
        "extra-skill",
    ]
    first_text = "\n".join(message.text for message in first_request.provider_request.messages)
    assert '<skill_content name="profile-skill">' in first_text
    assert '<skill_content name="extra-skill">' in first_text
    assert '<skill_content name="runtime-skill">' not in first_text

    second_request = provider.requests[1]
    assert second_request.metadata["active_skills"] == []
    assert second_request.metadata["skill_command"]["clear"] is True
    assert all("<skill_content" not in message.text for message in second_request.provider_request.messages)


@pytest.mark.asyncio
async def test_profile_active_skills_do_not_leak_to_next_plain_run(tmp_path: Path):
    _write_skill(tmp_path, "profile-skill")
    provider = ScriptedLLMProvider(
        [
            {"content": "Profile run."},
            {"content": "Plain run."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )
    profile = AgentProfile(name="review", active_skills=["profile-skill"])

    await runtime.run("Use profile skill.", session_id="session-profile", agent=profile)
    await runtime.run("Run plainly.", session_id="session-plain")

    first_text = "\n".join(message.text for message in provider.requests[0].provider_request.messages)
    second_text = "\n".join(message.text for message in provider.requests[1].provider_request.messages)
    assert '<skill_content name="profile-skill">' in first_text
    assert '<skill_content name="profile-skill">' not in second_text
    assert runtime.active_skills == []


@pytest.mark.asyncio
async def test_profile_active_skills_apply_when_profile_selected_by_command(
    tmp_path: Path,
):
    _write_skill(tmp_path, "review-skill")
    command_registry = CommandRegistry.from_sources(
        definitions=[
            CommandDefinition(
                name="review",
                content="Review $ARGUMENTS.",
                source="config",
                agent="review",
            )
        ]
    )
    agent_registry = AgentRegistry(
        [
            AgentProfile(
                name="review",
                prompt="Use the review profile.",
                active_skills=["review-skill"],
            )
        ],
        default_agent=None,
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        command_registry=command_registry,
        agent_registry=agent_registry,
    )

    await runtime.run("/review changes", session_id="session-command-profile-skill")

    request = provider.requests[0]
    assert request.metadata["selected_agent_source"] == "command"
    assert request.metadata["active_skills"] == ["review-skill"]
    request_text = "\n".join(
        message.text for message in request.provider_request.messages
    )
    assert "Use the review profile." in request_text
    assert '<skill_content name="review-skill">' in request_text
    assert runtime.active_skills == []


@pytest.mark.asyncio
async def test_profile_tools_apply_and_caller_tools_override_profile_tools():
    provider = ScriptedLLMProvider(
        [
            {"content": "First."},
            {"content": "Second."},
        ]
    )
    registry = AgentRegistry(
        [AgentProfile(name="limited", tools={"beta": False})],
        default_agent=None,
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1),
        tool_registry=ToolRegistry([_tool("alpha"), _tool("beta")]),
        agent_registry=registry,
    )

    await runtime.run("Use profile tools.", session_id="session-profile-tools", agent="limited")
    await runtime.run(
        "Override profile tools.",
        session_id="session-caller-tools",
        agent="limited",
        tools={"alpha": False, "beta": True},
    )

    assert [tool.id for tool in provider.requests[0].tools] == ["alpha"]
    assert provider.requests[0].metadata["enabled_tool_ids"] == ["alpha"]
    assert provider.requests[0].metadata["disabled_tool_ids"] == ["beta"]
    assert [tool.id for tool in provider.requests[1].tools] == ["beta"]
    assert provider.requests[1].metadata["enabled_tool_ids"] == ["beta"]
    assert provider.requests[1].metadata["disabled_tool_ids"] == ["alpha"]
    assert registry.resolve("limited").tools == {"beta": False}


@pytest.mark.asyncio
async def test_profile_permission_overlay_denies_base_allowed_tool():
    called: list[str] = []

    async def execute(args, context):
        called.append(context.session_id)
        return "ran"

    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-alpha", "alpha")]},
            {"content": "done"},
        ]
    )
    profile = AgentProfile(
        name="review",
        metadata={"permission": {"alpha": "deny"}},
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            max_iterations=2,
            tool_permissions={"alpha": "allow"},
        ),
        tool_registry=ToolRegistry([_tool("alpha", execute=execute)]),
    )

    result = await runtime.run(
        "Run alpha.",
        session_id="session-profile-permission",
        agent=profile,
    )

    history = runtime.store.read_history("session-profile-permission")
    tool_result = history[2].parts[0].tool_result

    assert result.status == LoopStatus.COMPLETED
    assert called == []
    assert tool_result is not None
    assert tool_result.status == "permission_denied"
    assert tool_result.error == "Permission denied by agent permission overlay: alpha"
    assert provider.requests[0].metadata["agent_permission_overlay"] == {
        "alpha": "deny"
    }
    assert provider.requests[0].provider_request.metadata[
        "agent_permission_overlay"
    ] == {"alpha": "deny"}
    assert profile.metadata == {"permission": {"alpha": "deny"}}


@pytest.mark.asyncio
async def test_runtime_permission_config_still_applies_without_profile_overlay():
    called: list[str] = []

    async def execute(args, context):
        called.append(context.session_id)
        return "ran"

    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-alpha", "alpha")]},
            {"content": "done"},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2, tool_permissions={"alpha": "deny"}),
        tool_registry=ToolRegistry([_tool("alpha", execute=execute)]),
    )

    result = await runtime.run(
        "Run alpha.",
        session_id="session-runtime-permission",
    )

    history = runtime.store.read_history("session-runtime-permission")
    tool_result = history[2].parts[0].tool_result

    assert result.status == LoopStatus.COMPLETED
    assert called == []
    assert tool_result is not None
    assert tool_result.status == "permission_denied"
    assert tool_result.error == "Permission denied by runtime config: alpha"
    assert "agent_permission_overlay" not in provider.requests[0].metadata


@pytest.mark.asyncio
async def test_invalid_profile_permission_metadata_raises_before_provider_call():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    profile = AgentProfile(
        name="bad-permission",
        metadata={"permission": {"alpha": "block"}},
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=1),
        tool_registry=ToolRegistry([_tool("alpha")]),
    )

    with pytest.raises(ValueError, match="agent profile permission"):
        await runtime.run(
            "Do not call provider.",
            session_id="session-invalid-profile-permission",
            agent=profile,
        )

    assert provider.requests == []


@pytest.mark.asyncio
async def test_profile_permission_ask_remains_pending_until_approved():
    called: list[str] = []

    async def execute(args, context):
        called.append(context.session_id)
        return "ran"

    provider = ScriptedLLMProvider(
        [{"tool_calls": [_tool_call("call-alpha", "alpha")]}]
    )
    profile = AgentProfile(
        name="review",
        metadata={"permission": {"alpha": "ask"}},
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=2, tool_permissions={"alpha": "allow"}),
        tool_registry=ToolRegistry([_tool("alpha", execute=execute)]),
    )

    first = await runtime.run(
        "Run alpha.",
        session_id="session-profile-permission-ask",
        agent=profile,
    )
    resumed = await runtime.resume("session-profile-permission-ask")

    assert first.status == LoopStatus.WAITING_FOR_PERMISSION
    assert resumed.status == LoopStatus.WAITING_FOR_PERMISSION
    assert resumed.pending_permission_request == first.pending_permission_request
    assert called == []
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_profile_max_iterations_overrides_run_limit_without_mutating_config():
    provider = ScriptedLLMProvider(
        [{"tool_calls": [_tool_call("call-alpha", "alpha")]}]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(max_iterations=3),
        tool_registry=ToolRegistry([_tool("alpha")]),
    )
    profile = AgentProfile(name="looper", max_iterations=1)

    result = await runtime.run("Loop once.", session_id="session-max", agent=profile)

    assert result.status == LoopStatus.MAX_ITERATIONS
    assert runtime.config.max_iterations == 3
    request = provider.requests[0]
    assert request.max_iterations == 1
    assert request.metadata["max_iterations"] == 1
    assert request.metadata["agent_max_iterations"] == 1
    assert request.provider_request.metadata["loop"]["max_iterations"] == 1
    run_start = next(event for event in result.runtime_events if event.type == "run_start")
    assert run_start.payload["max_iterations"] == 1


@pytest.mark.asyncio
async def test_unknown_agent_error_includes_requested_and_available_agents():
    provider = ScriptedLLMProvider([{"content": "unused"}])
    registry = AgentRegistry(
        [AgentProfile(name="general"), AgentProfile(name="review")],
        default_agent="general",
    )
    runtime = AgentRuntime(provider=provider, agent_registry=registry)

    with pytest.raises(KeyError) as error:
        await runtime.run("Use missing.", session_id="session-missing-agent", agent="missing")

    error_text = str(error.value)
    assert "missing" in error_text
    assert "general" in error_text
    assert "review" in error_text
    assert provider.requests == []


@pytest.mark.asyncio
async def test_profile_metadata_is_run_metadata_without_top_level_model_switch():
    provider = ScriptedLLMProvider([{"content": "Metadata."}])
    profile = AgentProfile(
        name="review",
        metadata={"model": "profile-model", "mode": "review", "temperature": 0.2},
    )
    runtime = AgentRuntime(provider=provider, config=RuntimeConfig(max_iterations=1))

    await runtime.run("Record metadata.", session_id="session-profile-metadata", agent=profile)

    request = provider.requests[0]
    assert request.metadata["agent_metadata"] == {
        "model": "profile-model",
        "mode": "review",
        "temperature": 0.2,
    }
    assert "model" not in request.metadata
    assert "mode" not in request.metadata
    assert "temperature" not in request.metadata


def test_agent_runtime_profile_import_boundary():
    code = """
import json
import sys

from efp_runtime.agents import AgentProfile, AgentRegistry
from efp_runtime.runtime import AgentRuntime

AgentRegistry([AgentProfile(name="review")], default_agent=None)

blocked = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps({
    "agent_runtime": AgentRuntime.__name__,
    "legacy_loaded": [name for name in blocked if name in sys.modules],
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
    assert payload == {"agent_runtime": "AgentRuntime", "legacy_loaded": []}


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


def _message_text(message) -> str:
    return "\n".join(part.text or "" for part in message.parts)


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
