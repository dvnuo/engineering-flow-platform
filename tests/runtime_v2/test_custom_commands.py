from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.agents.profile import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.commands import CommandRegistry
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.runtime.agent import _resolve_config


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
    assert commands["fix"].metadata["tools"] == ["read_file", "edit"]
    assert commands["fix"].command_file == second_dir / "fix.md"
    assert commands["review:pr"].description == "Review a PR"
    assert commands["review:pr"].content == "# Review\nFocus on correctness."


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
    assert request.provider_request.metadata["command_name"] == "fix"


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
    assert '<skill_content name="review-pr">' in request.provider_request.messages[0].text
    assert request.provider_request.messages[1].role == "user"
    assert request.provider_request.messages[1].text == "Please inspect the diff."
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
