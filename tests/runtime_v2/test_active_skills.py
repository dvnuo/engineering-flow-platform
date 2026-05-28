from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.models import MessageRole
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.skills.context import SkillContextBuilder
from efp_runtime.skills.discovery import SkillDiscovery


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_config_active_skills_are_injected_before_user_history(tmp_path: Path):
    _write_skill(tmp_path, "review-pr", content="# Review\nCheck diffs.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            active_skills=["review-pr"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("Use the configured skill.", session_id="session-active")

    request = provider.requests[0]
    provider_messages = request.provider_request.messages
    assert provider_messages[0].role == "system"
    assert provider_messages[0].text.startswith('<skill_content name="review-pr">')
    assert "# Skill: review-pr" in provider_messages[0].text
    assert provider_messages[1].role == "user"
    assert provider_messages[1].text == "Use the configured skill."
    assert [message.role for message in request.messages] == [MessageRole.USER]
    assert request.metadata["active_skills"] == ["review-pr"]
    assert request.provider_request.metadata["active_skills"] == ["review-pr"]


@pytest.mark.asyncio
async def test_injected_skill_context_builder_is_used_without_config_directories(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            active_skills=["review-pr"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
        skill_context_builder=SkillContextBuilder(SkillDiscovery([tmp_path])),
    )

    await runtime.run("Use injected builder.", session_id="session-builder")

    request = provider.requests[0]
    assert request.provider_request.messages[0].role == "system"
    assert '<skill_content name="review-pr">' in request.provider_request.messages[0].text


@pytest.mark.asyncio
async def test_skill_command_adds_active_skill_and_cleans_user_text(tmp_path: Path):
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

    await runtime.run("/skill review-pr\nPlease inspect the diff.", session_id="session-command")

    request = provider.requests[0]
    assert runtime.active_skills == ["review-pr"]
    assert request.provider_request.messages[0].role == "system"
    assert request.provider_request.messages[1].text == "Please inspect the diff."
    assert request.messages[0].parts[0].text == "Please inspect the diff."
    assert request.metadata["skill_command"] == {
        "add": ["review-pr"],
        "clear": False,
        "cleaned_text": "Please inspect the diff.",
    }


@pytest.mark.asyncio
async def test_skill_command_only_still_sends_context(tmp_path: Path):
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

    await runtime.run("/skill review-pr", session_id="session-command-only")

    request = provider.requests[0]
    assert [message.role for message in request.provider_request.messages] == ["system"]
    assert "# Skill: review-pr" in request.provider_request.messages[0].text
    assert [message.role for message in request.messages] == [MessageRole.USER]
    assert request.messages[0].parts == []


@pytest.mark.asyncio
async def test_skill_clear_removes_active_skills_and_stops_injection(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            active_skills=["review-pr"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("/skill clear\nContinue without it.", session_id="session-clear")

    request = provider.requests[0]
    assert runtime.active_skills == []
    assert [message.role for message in request.provider_request.messages] == ["user"]
    assert request.provider_request.messages[0].text == "Continue without it."
    assert request.metadata["active_skills"] == []
    assert request.metadata["skill_command"]["clear"] is True


@pytest.mark.asyncio
async def test_skill_context_is_not_persisted_or_duplicated_between_runs(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider(
        [
            {"content": "First answer."},
            {"content": "Second answer."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            active_skills=["review-pr"],
            max_iterations=1,
        ),
    )

    await runtime.run("First request.", session_id="session-history")
    first_history = runtime.store.read_history("session-history")
    assert [message.role for message in first_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

    await runtime.run("Second request.", session_id="session-history")

    second_request = provider.requests[1]
    skill_context_count = sum(
        1
        for message in second_request.provider_request.messages
        if message.role == "system" and "<skill_content" in message.text
    )
    assert skill_context_count == 1
    assert [message.role for message in second_request.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]

    stored_history = runtime.store.read_history("session-history")
    assert [message.role for message in stored_history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all(message.role is not MessageRole.SYSTEM for message in stored_history)


@pytest.mark.asyncio
async def test_unknown_active_skill_error_includes_available_names(tmp_path: Path):
    _write_skill(tmp_path, "known-skill")
    provider = ScriptedLLMProvider([{"content": "Unused."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(skill_directories=[tmp_path], max_iterations=1),
    )

    with pytest.raises(KeyError) as error:
        await runtime.run("/skill missing\nUse it.", session_id="session-missing")

    error_text = str(error.value)
    assert "Unknown skill: missing" in error_text
    assert "Available skills: known-skill" in error_text
    assert provider.requests == []


def test_active_skill_runtime_imports_do_not_load_legacy_modules():
    code = """
import json
import sys

import efp_runtime.runtime
import efp_runtime.skills.context
import efp_runtime.skills.commands

blocked = [
    "src.agents.core",
    "src.agents.skill_runtime",
    "src.agents.skill_mode",
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
