from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.agents import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import MessageRole
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.system_prompt import DEFAULT_SYSTEM_PROMPT, SystemPromptBuilder


ROOT = Path(__file__).resolve().parents[2]


def test_default_system_prompt_contains_coding_agent_operating_rules():
    assert "interactive software engineering agent" in DEFAULT_SYSTEM_PROMPT
    assert "do not invent command output" in DEFAULT_SYSTEM_PROMPT
    assert "file contents, tool results, or runtime state" in DEFAULT_SYSTEM_PROMPT
    assert "Read or search relevant code before editing" in DEFAULT_SYSTEM_PROMPT
    assert "concise and direct, like a CLI coding agent" in DEFAULT_SYSTEM_PROMPT
    assert "Preserve user changes" in DEFAULT_SYSTEM_PROMPT
    assert "Do not commit changes unless the user explicitly asks" in DEFAULT_SYSTEM_PROMPT
    assert "engineering-flow-platform-tools binaries on PATH" in DEFAULT_SYSTEM_PROMPT
    assert "`jira`, `confluence`, `browser`" in DEFAULT_SYSTEM_PROMPT
    assert "built from `cmd/<tool>`" in DEFAULT_SYSTEM_PROMPT
    assert "EFP `bash` built-in" in DEFAULT_SYSTEM_PROMPT
    assert "workspace-full-access" in DEFAULT_SYSTEM_PROMPT
    assert "<tool> commands --json" in DEFAULT_SYSTEM_PROMPT
    assert "<tool> schema <command> --json" in DEFAULT_SYSTEM_PROMPT
    assert "`--dry-run`" in DEFAULT_SYSTEM_PROMPT
    assert "`--yes`" in DEFAULT_SYSTEM_PROMPT
    assert "not model-facing function tools" in DEFAULT_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_default_runtime_injects_only_agents_instruction_before_skills(
    tmp_path: Path,
):
    (tmp_path / "AGENTS.md").write_text("Project instructions.", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Claude instructions.", encoding="utf-8")
    (tmp_path / "CONTEXT.md").write_text("Context instructions.", encoding="utf-8")
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "review-pr", content="# Review\nInspect diffs.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            active_skills=["review-pr"],
            max_iterations=2,
        ),
    )

    result = await runtime.run("Inspect this.", session_id="session-default-agents-only")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    messages = request.provider_request.messages
    assert [message.role for message in messages] == ["system", "system", "system", "user"]
    assert messages[0].text.startswith("Instructions from:")
    assert "Project instructions." in messages[0].text
    assert "Claude instructions." not in "\n".join(message.text for message in messages)
    assert "Context instructions." not in "\n".join(message.text for message in messages)
    assert DEFAULT_SYSTEM_PROMPT.strip() not in "\n".join(message.text for message in messages)
    assert "Environment:" not in "\n".join(message.text for message in messages)
    assert "Runtime reminders:" not in "\n".join(message.text for message in messages)
    assert "<available_skills>" in messages[1].text
    assert messages[2].text.startswith('<skill_content name="review-pr">')
    assert messages[3].text == "Inspect this."
    assert request.metadata["system_prompt_context_count"] == 0
    assert request.metadata["environment_context_count"] == 0
    assert request.metadata["instruction_context_count"] == 1
    assert request.metadata["available_skill_context_count"] == 1
    assert request.metadata["skill_context_count"] == 1
    assert request.provider_request.metadata["system_prompt_context_count"] == 0
    assert request.provider_request.metadata["environment_context_count"] == 0
    assert request.provider_request.metadata["instruction_context_count"] == 1
    assert request.provider_request.metadata["available_skill_context_count"] == 1
    assert request.provider_request.metadata["skill_context_count"] == 1


@pytest.mark.asyncio
async def test_opt_in_runtime_prepends_system_prompt_before_instructions_and_skills(
    tmp_path: Path,
):
    (tmp_path / "AGENTS.md").write_text("Project instructions.", encoding="utf-8")
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "review-pr", content="# Review\nInspect diffs.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            active_skills=["review-pr"],
            max_iterations=2,
            include_default_system_prompt=True,
            include_environment_context=True,
            include_runtime_reminders=True,
        ),
    )

    result = await runtime.run("Inspect this.", session_id="session-system-stack")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    messages = request.provider_request.messages
    assert messages[0].role == "system"
    assert "EFP runtime" in messages[0].text

    environment_index = _message_index(messages, "Environment:")
    instruction_index = _message_index(messages, "Instructions from:")
    available_skill_index = _message_index(messages, "<available_skills>")
    skill_index = _message_index(messages, '<skill_content name="review-pr">')
    user_index = _message_index(messages, "Inspect this.")
    assert 0 < environment_index < instruction_index
    assert instruction_index < available_skill_index < skill_index < user_index
    environment_message = messages[environment_index]
    assert environment_message.metadata["message_metadata"]["source"] == (
        "environment_context"
    )
    assert environment_message.metadata["message_metadata"]["kind"] == (
        "environment_context"
    )
    assert "- model: github-copilot/gpt-5.4" in environment_message.text
    assert f"- working directory: {tmp_path.resolve()}" in environment_message.text
    assert f"- workspace root: {tmp_path.resolve()}" in environment_message.text
    assert "- git repository: false" in environment_message.text
    assert f"- platform: {sys.platform}" in environment_message.text
    assert re.search(r"- date: \d{4}-\d{2}-\d{2}", environment_message.text)
    assert request.metadata["system_prompt_context_count"] == 3
    assert request.metadata["environment_context_count"] == 1
    assert request.metadata["environment_context_model"] == "github-copilot/gpt-5.4"
    assert request.metadata["instruction_context_count"] == 1
    assert request.metadata["available_skill_context_count"] == 1
    assert request.metadata["skill_context_count"] == 1
    assert request.provider_request.metadata["system_prompt_context_count"] == 3
    assert request.provider_request.metadata["environment_context_count"] == 1
    assert request.provider_request.metadata["environment_context_model"] == (
        "github-copilot/gpt-5.4"
    )
    assert request.provider_request.metadata["instruction_context_count"] == 1
    assert request.provider_request.metadata["available_skill_context_count"] == 1
    assert request.provider_request.metadata["skill_context_count"] == 1


@pytest.mark.asyncio
async def test_available_skills_context_is_injected_without_active_skill(
    tmp_path: Path,
):
    skills_root = tmp_path / "skills"
    _write_skill(
        skills_root,
        "review-pr",
        description="Review <pull> & requests",
    )
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            include_default_system_prompt=False,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    result = await runtime.run("Inspect this.", session_id="session-available-skills")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    messages = request.provider_request.messages
    assert messages[-1].role == "user"
    assert messages[-1].text == "Inspect this."
    available_message = messages[_message_index(messages, "<available_skills>")]
    assert available_message.metadata["message_metadata"]["kind"] == "available_skills"
    assert (
        available_message.metadata["message_metadata"]["source"] == "available_skills"
    )
    assert (
        available_message.parts[0].metadata["part_metadata"]["kind"]
        == "available_skills"
    )
    assert (
        "Skills provide specialized instructions and workflows for specific tasks."
        in available_message.text
    )
    assert (
        "Use the skill tool to load a skill when a task matches its description."
        in available_message.text
    )
    assert "<name>review-pr</name>" in available_message.text
    assert (
        "<description>Review &lt;pull&gt; &amp; requests</description>"
        in available_message.text
    )
    assert request.metadata["available_skill_context_count"] == 1
    assert request.metadata["skill_context_count"] == 0
    assert request.provider_request.metadata["available_skill_context_count"] == 1
    assert request.provider_request.metadata["skill_context_count"] == 0
    history = runtime.store.read_history("session-available-skills")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


@pytest.mark.asyncio
async def test_available_skills_context_hides_denied_skills(tmp_path: Path):
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "internal-docs", description="Internal docs")
    _write_skill(skills_root, "public-docs", description="Public docs")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            tool_permissions={"skill": {"internal-*": "deny"}},
            include_default_system_prompt=False,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    result = await runtime.run("Use docs.", session_id="session-hidden-skills")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    available_message = request.provider_request.messages[
        _message_index(request.provider_request.messages, "<available_skills>")
    ]
    assert "<name>public-docs</name>" in available_message.text
    assert "<description>Public docs</description>" in available_message.text
    assert "internal-docs" not in available_message.text
    assert "Internal docs" not in available_message.text
    assert request.metadata["available_skill_context_count"] == 1
    assert request.metadata["available_skill_count"] == 1


@pytest.mark.asyncio
async def test_available_skills_context_is_omitted_when_no_skills(tmp_path: Path):
    skills_root = tmp_path / "skills"
    skills_root.mkdir()
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            include_default_system_prompt=False,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    result = await runtime.run("No skills.", session_id="session-no-skills")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert "<available_skills>" not in "\n".join(
        message.text for message in request.provider_request.messages
    )
    assert request.provider_request.messages[-1].role == "user"
    assert request.metadata["available_skill_context_count"] == 0
    assert request.provider_request.metadata["available_skill_context_count"] == 0


@pytest.mark.asyncio
async def test_default_system_prompt_can_be_disabled_but_explicit_texts_remain(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
            system_prompt_texts=["Custom system layer."],
            max_iterations=2,
        ),
    )

    await runtime.run("Use custom prompt.", session_id="session-custom-system")

    messages = provider.requests[0].provider_request.messages
    assert [message.role for message in messages] == ["system", "user"]
    assert messages[0].text == "Custom system layer."
    assert DEFAULT_SYSTEM_PROMPT.strip() not in messages[0].text
    assert provider.requests[0].metadata["system_prompt_context_count"] == 1


def test_system_prompt_paths_load_workspace_files_with_truncation_metadata(
    tmp_path: Path,
):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt_file = prompt_dir / "base.txt"
    prompt_file.write_text("abcdef", encoding="utf-8")

    messages = SystemPromptBuilder(
        workspace_root=tmp_path,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=False,
        system_prompt_paths=["prompts/base.txt"],
        max_system_prompt_chars=3,
    ).build_messages()

    assert len(messages) == 1
    message = messages[0]
    assert message.role is MessageRole.SYSTEM
    assert message.metadata["context_type"] == "system_prompt"
    assert message.metadata["source"] == "file"
    assert message.metadata["path"] == str(prompt_file.resolve())
    assert message.metadata["truncated"] is True
    assert message.metadata["original_chars"] == 6
    assert message.parts[0].metadata == message.metadata
    assert "abc" in message.parts[0].text
    assert "truncated to 3 of 6 chars" in message.parts[0].text


def test_environment_inline_and_file_prompts_appear_before_runtime_reminders(
    tmp_path: Path,
):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt_file = prompt_dir / "team.txt"
    prompt_file.write_text("Team file prompt.", encoding="utf-8")

    messages = SystemPromptBuilder(
        workspace_root=tmp_path,
        include_default_system_prompt=True,
        include_environment_context=True,
        system_prompt_texts=["Inline prompt."],
        system_prompt_paths=["prompts/team.txt"],
        include_runtime_reminders=True,
    ).build_messages(metadata={"max_iterations": 2})

    assert [message.role for message in messages] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
    ]
    assert [message.metadata["source"] for message in messages] == [
        "default_system_prompt",
        "environment_context",
        "inline",
        "file",
        "runtime_reminders",
    ]
    assert "EFP runtime" in messages[0].parts[0].text
    assert "Environment:" in messages[1].parts[0].text
    assert messages[2].parts[0].text == "Inline prompt."
    assert messages[3].parts[0].text == "Team file prompt."
    assert "max_iterations=2" in messages[4].parts[0].text


def test_system_prompt_paths_reject_path_traversal_and_outside_files(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-system.txt"
    outside.write_text("outside secret", encoding="utf-8")

    messages = SystemPromptBuilder(
        workspace_root=tmp_path,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=False,
        system_prompt_paths=["../outside-system.txt", outside],
    ).build_messages()

    assert messages == []


def test_runtime_reminders_can_be_enabled_and_disabled():
    enabled = SystemPromptBuilder(
        workspace_root=None,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=True,
    ).build_messages(
        metadata={
            "max_iterations": 3,
            "enable_question_tool": True,
            "tool_output_truncation_enabled": True,
        }
    )

    assert len(enabled) == 1
    reminder = enabled[0]
    assert reminder.metadata["source"] == "runtime_reminders"
    assert "close-bounded by max_iterations=3" in reminder.parts[0].text
    assert "avoid extra provider or tool loops" in reminder.parts[0].text
    assert "only when truly blocked after reading relevant context" in reminder.parts[0].text
    assert "output_path" in reminder.parts[0].text
    assert "saved output metadata" in reminder.parts[0].text
    assert "ranged read or grep" in reminder.parts[0].text

    unbounded = SystemPromptBuilder(
        workspace_root=None,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=True,
    ).build_messages(
        metadata={
            "max_iterations_unbounded": True,
            "enable_question_tool": True,
        }
    )
    assert len(unbounded) == 1
    assert "close-bounded by max_iterations" not in unbounded[0].parts[0].text
    assert "only when truly blocked after reading relevant context" in unbounded[0].parts[0].text

    disabled = SystemPromptBuilder(
        workspace_root=None,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=False,
    ).build_messages(metadata={"max_iterations": 3, "enable_question_tool": True})
    assert disabled == []


def test_plan_mode_reminder_is_read_only_and_finishes_with_plan_exit():
    messages = SystemPromptBuilder(
        workspace_root=None,
        include_default_system_prompt=False,
        include_environment_context=False,
        include_runtime_reminders=True,
    ).build_messages(metadata={"runtime_mode": "plan"})

    assert len(messages) == 1
    text = messages[0].parts[0].text
    assert "Plan mode is active" in text
    assert "read-only analysis" in text
    assert "do not write files" in text
    assert "do not run shell commands that mutate state" in text
    assert "plan_exit" in text


@pytest.mark.asyncio
async def test_system_prompt_context_is_not_persisted_or_duplicated_between_runs(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider(
        [
            {"content": "First answer."},
            {"content": "Second answer."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            include_default_system_prompt=True,
            include_environment_context=True,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    await runtime.run("First request.", session_id="session-system-history")
    await runtime.run("Second request.", session_id="session-system-history")

    history = runtime.store.read_history("session-system-history")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all(message.role is not MessageRole.SYSTEM for message in history)

    second_messages = provider.requests[1].provider_request.messages
    default_prompt_count = sum(
        1 for message in second_messages if "EFP runtime" in message.text
    )
    environment_count = sum(
        1 for message in second_messages if "Environment:" in message.text
    )
    assert default_prompt_count == 1
    assert environment_count == 1
    assert [message.role for message in provider.requests[1].messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert all(
        "Environment:" not in part.text
        for message in history
        for part in message.parts
        if part.text is not None
    )


def test_environment_context_builder_contains_runtime_environment(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    cwd = tmp_path / "src"

    messages = SystemPromptBuilder(
        workspace_root=tmp_path,
        include_default_system_prompt=False,
        include_environment_context=True,
        include_runtime_reminders=False,
    ).build_messages(
        metadata={
            "requested_model": "github-copilot/gpt-5.4",
            "cwd": cwd,
        }
    )

    assert len(messages) == 1
    message = messages[0]
    assert message.metadata["source"] == "environment_context"
    assert message.metadata["kind"] == "environment_context"
    assert message.metadata["model_id"] == "github-copilot/gpt-5.4"
    assert message.metadata["workspace_root"] == str(tmp_path.resolve())
    assert message.metadata["git_repository"] is True
    assert message.parts[0].metadata == message.metadata
    text = message.parts[0].text
    assert text.startswith("Environment:\n")
    assert "- model: github-copilot/gpt-5.4" in text
    assert f"- working directory: {cwd.resolve()}" in text
    assert f"- workspace root: {tmp_path.resolve()}" in text
    assert "- git repository: true" in text
    assert f"- platform: {sys.platform}" in text
    assert re.search(r"- date: \d{4}-\d{2}-\d{2}", text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_model", "expected_model"),
    [
        ("github-copilot/gpt-5.4", "github-copilot/gpt-5.4"),
        ("gpt-5.4", "github-copilot/gpt-5.4"),
    ],
)
async def test_environment_context_uses_requested_model(
    tmp_path: Path,
    requested_model: str,
    expected_model: str,
):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            include_default_system_prompt=False,
            include_environment_context=True,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    await runtime.run(
        "Use requested model.",
        session_id=f"session-env-{requested_model.replace('/', '-')}",
        metadata={"requested_model": requested_model},
    )

    request = provider.requests[0]
    environment_message = request.provider_request.messages[0]
    assert environment_message.metadata["message_metadata"]["kind"] == (
        "environment_context"
    )
    assert f"- model: {expected_model}" in environment_message.text
    assert request.metadata["environment_context_count"] == 1
    assert request.metadata["environment_context_model"] == expected_model


@pytest.mark.asyncio
async def test_environment_context_can_be_disabled(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            include_environment_context=False,
            include_runtime_reminders=False,
            max_iterations=2,
        ),
    )

    await runtime.run("No environment.", session_id="session-no-env")

    request = provider.requests[0]
    assert all(
        "Environment:" not in message.text
        for message in request.provider_request.messages
    )
    assert request.metadata["environment_context_count"] == 0
    assert "environment_context_model" not in request.metadata
    assert request.provider_request.metadata["environment_context_count"] == 0


def test_child_config_preserves_system_prompt_settings(tmp_path: Path):
    base_config = RuntimeConfig(
        workspace_root=tmp_path,
        include_default_system_prompt=False,
        include_environment_context=False,
        system_prompt_texts=["Child system prompt."],
        system_prompt_paths=["prompts/child.md"],
        max_system_prompt_chars=17,
        include_runtime_reminders=False,
    )

    child = _child_config(
        profile=AgentProfile(name="general"),
        base_config=base_config,
        workspace_root=None,
        metadata={"task_id": "task-system"},
    )

    assert child.include_default_system_prompt is False
    assert child.include_environment_context is False
    assert child.system_prompt_texts == ["Child system prompt."]
    assert child.system_prompt_paths == ["prompts/child.md"]
    assert child.max_system_prompt_chars == 17
    assert child.include_runtime_reminders is False
    assert base_config.system_prompt_texts == ["Child system prompt."]


def test_system_prompt_import_boundary():
    code = """
import json
import sys

import efp_runtime.runtime
import efp_runtime.system_prompt

blocked = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
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


def _message_index(messages, text: str) -> int:
    for index, message in enumerate(messages):
        if text in message.text:
            return index
    raise AssertionError(f"message containing {text!r} not found")


def _write_skill(
    tmp_path: Path,
    name: str,
    *,
    description: str = "Loads skill context",
    content: str = "# Skill\nUse this context.",
) -> Path:
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir
