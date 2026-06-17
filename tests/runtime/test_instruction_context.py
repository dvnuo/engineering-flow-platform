from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.instructions import InstructionContextBuilder
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.models import MessagePart, MessageRole
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry


ROOT = Path(__file__).resolve().parents[2]


def test_builder_loads_agents_as_only_workspace_default_file(tmp_path: Path):
    (tmp_path / "CONTEXT.md").write_text("Context instructions.", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Agent instructions.", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Claude instructions.", encoding="utf-8")

    messages = InstructionContextBuilder(workspace_root=tmp_path).build_messages()

    assert [message.role for message in messages] == [MessageRole.SYSTEM]
    assert [message.metadata["path"] for message in messages] == [
        str((tmp_path / "AGENTS.md").resolve())
    ]
    assert messages[0].parts[0].text.splitlines()[0] == (
        f"Instructions from: {(tmp_path / 'AGENTS.md').resolve()}"
    )
    assert messages[0].metadata == {
        "kind": "instruction_context",
        "source": "file",
        "path": str((tmp_path / "AGENTS.md").resolve()),
        "truncated": False,
        "original_chars": len("Agent instructions."),
    }
    assert messages[0].parts[0].metadata == messages[0].metadata
    assert "Claude instructions." not in messages[0].parts[0].text
    assert "Context instructions." not in messages[0].parts[0].text


def test_builder_loads_workspace_instruction_glob_after_agents(tmp_path: Path):
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("Agent instructions.", encoding="utf-8")
    instructions = tmp_path / ".efp" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "b.instructions.md").write_text("B rules.", encoding="utf-8")
    (instructions / "a.instructions.md").write_text("A rules.", encoding="utf-8")
    (instructions / "ignored.md").write_text("Ignored.", encoding="utf-8")

    messages = InstructionContextBuilder(workspace_root=tmp_path).build_messages(
        metadata={"cwd": nested}
    )

    assert [message.metadata["path"] for message in messages] == [
        str((tmp_path / "AGENTS.md").resolve()),
        str((instructions / "a.instructions.md").resolve()),
        str((instructions / "b.instructions.md").resolve()),
    ]
    text = "\n".join(message.parts[0].text for message in messages)
    assert "Agent instructions." in text
    assert "A rules." in text
    assert "B rules." in text
    assert "Ignored." not in text


def test_nested_cwd_ignores_claude_and_context_by_default(tmp_path: Path):
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("Workspace agents.", encoding="utf-8")
    (tmp_path / "src" / "CLAUDE.md").write_text("Source claude.", encoding="utf-8")
    (nested / "CONTEXT.md").write_text("Package context.", encoding="utf-8")

    messages = InstructionContextBuilder(workspace_root=tmp_path).build_messages(
        metadata={"cwd": nested}
    )

    assert len(messages) == 1
    assert messages[0].metadata["path"] == str((tmp_path / "AGENTS.md").resolve())
    text = messages[0].parts[0].text
    assert "Workspace agents." in text
    assert "Source claude." not in text
    assert "Package context." not in text


def test_explicit_instruction_paths_support_relative_absolute_and_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    relative = tmp_path / "relative.md"
    relative.write_text("Relative instructions.", encoding="utf-8")
    absolute = tmp_path / "absolute.md"
    absolute.write_text("Absolute instructions.", encoding="utf-8")
    skipped_directory = tmp_path / "directory.md"
    skipped_directory.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    home_file = home / "home.md"
    home_file.write_text("Home instructions.", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    messages = InstructionContextBuilder(
        workspace_root=tmp_path,
        include_default_files=False,
        instruction_paths=[
            "relative.md",
            absolute,
            "~/home.md",
            "missing.md",
            "directory.md",
            relative.resolve(),
        ],
    ).build_messages()

    assert [message.metadata["path"] for message in messages] == [
        str(relative.resolve()),
        str(absolute.resolve()),
        str(home_file.resolve()),
    ]
    assert [message.parts[0].text.splitlines()[1] for message in messages] == [
        "Relative instructions.",
        "Absolute instructions.",
        "Home instructions.",
    ]


def test_explicit_instruction_paths_support_globs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "b.instructions.md").write_text("B explicit.", encoding="utf-8")
    (rules / "a.instructions.md").write_text("A explicit.", encoding="utf-8")
    (rules / "ignored.md").write_text("Ignored explicit.", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "home.instructions.md").write_text("Home explicit.", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    messages = InstructionContextBuilder(
        workspace_root=tmp_path,
        include_default_files=False,
        instruction_paths=[
            "rules/*.instructions.md",
            str(rules / "*.instructions.md"),
            "~/*.instructions.md",
        ],
    ).build_messages()

    assert [message.metadata["path"] for message in messages] == [
        str((rules / "a.instructions.md").resolve()),
        str((rules / "b.instructions.md").resolve()),
        str((home / "home.instructions.md").resolve()),
    ]
    text = "\n".join(message.parts[0].text for message in messages)
    assert "A explicit." in text
    assert "B explicit." in text
    assert "Home explicit." in text
    assert "Ignored explicit." not in text


def test_inline_instruction_text_generates_system_message():
    messages = InstructionContextBuilder(
        workspace_root=None,
        instruction_texts=["Inline instructions.", "  "],
    ).build_messages()

    assert len(messages) == 1
    assert messages[0].role is MessageRole.SYSTEM
    assert messages[0].parts[0].text == "Inline instructions."
    assert messages[0].metadata == {
        "kind": "instruction_context",
        "source": "inline",
        "truncated": False,
        "original_chars": len("Inline instructions."),
    }
    assert "path" not in messages[0].metadata


def test_long_instruction_is_truncated_with_metadata(tmp_path: Path):
    content = "abcdef"
    (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")

    messages = InstructionContextBuilder(
        workspace_root=tmp_path,
        max_instruction_chars=3,
    ).build_messages()

    message = messages[0]
    assert message.metadata["truncated"] is True
    assert message.metadata["original_chars"] == len(content)
    assert "abc" in message.parts[0].text
    assert "truncated to 3 of 6 chars" in message.parts[0].text


@pytest.mark.asyncio
async def test_agent_runtime_run_injects_instruction_context_without_persisting_it(
    tmp_path: Path,
):
    (tmp_path / "AGENTS.md").write_text("Prefer precise answers.", encoding="utf-8")
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

    await runtime.run("Use instructions.", session_id="session-instructions")

    request = provider.requests[0]
    provider_messages = request.provider_request.messages
    assert [message.role for message in provider_messages] == ["system", "user"]
    assert provider_messages[0].text.startswith(
        f"Instructions from: {(tmp_path / 'AGENTS.md').resolve()}\n"
    )
    assert "Prefer precise answers." in provider_messages[0].text
    assert provider_messages[1].text == "Use instructions."
    assert request.metadata["instruction_context_count"] == 1
    assert request.provider_request.metadata["instruction_context_count"] == 1

    history = runtime.store.read_history("session-instructions")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all(message.role is not MessageRole.SYSTEM for message in history)


@pytest.mark.asyncio
async def test_agent_runtime_refreshes_instruction_context_each_provider_request(
    tmp_path: Path,
):
    instructions = tmp_path / ".efp" / "instructions"
    instructions.mkdir(parents=True)
    rule_file = instructions / "rules.instructions.md"
    rule_file.write_text("Initial rules.", encoding="utf-8")

    async def refresh_rules(_args, _context):
        rule_file.write_text("Updated rules.", encoding="utf-8")
        return "updated"

    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-refresh", "refresh_rules")]},
            {"content": "Done."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        tool_registry=ToolRegistry(
            [
                ToolDef(
                    id="refresh_rules",
                    description="Refresh instruction rules.",
                    input_schema={"type": "object", "properties": {}},
                    execute=refresh_rules,
                )
            ]
        ),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("Refresh instructions.", session_id="session-refresh-rules")

    assert len(provider.requests) == 2
    first_text = "\n".join(
        message.text for message in provider.requests[0].provider_request.messages
    )
    second_text = "\n".join(
        message.text for message in provider.requests[1].provider_request.messages
    )
    assert "Initial rules." in first_text
    assert "Updated rules." not in first_text
    assert "Updated rules." in second_text
    assert provider.requests[1].metadata["instruction_context_count"] == 1
    assert provider.requests[1].metadata["system_instruction_paths"] == [
        str(rule_file.resolve())
    ]


@pytest.mark.asyncio
async def test_resume_injects_instruction_context_without_empty_user_message(
    tmp_path: Path,
):
    (tmp_path / "AGENTS.md").write_text("Resume instructions.", encoding="utf-8")
    store = InMemorySessionStore()
    store.create_session(session_id="session-resume-instructions")
    store.append_message(
        "session-resume-instructions",
        role="user",
        parts=[MessagePart.text_part("Existing request.")],
        status="complete",
    )
    store.append_message(
        "session-resume-instructions",
        role="assistant",
        parts=[MessagePart.text_part("Existing answer.")],
        status="complete",
    )
    provider = ScriptedLLMProvider([{"content": "Resumed."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
        store=store,
    )

    await runtime.resume("session-resume-instructions")

    request = provider.requests[0]
    assert [message.role for message in request.provider_request.messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert "Resume instructions." in request.provider_request.messages[0].text
    assert request.metadata["instruction_context_count"] == 1

    history = store.read_history("session-resume-instructions")
    assert [message.role for message in history] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.ASSISTANT,
    ]
    assert history[0].parts[0].text == "Existing request."


@pytest.mark.asyncio
async def test_instruction_context_precedes_active_skill_context(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("Project instructions.", encoding="utf-8")
    skills_root = tmp_path / "skills"
    _write_skill(skills_root, "review-pr", content="# Review\nCheck diffs.")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_root],
            active_skills=["review-pr"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_environment_context=False,
            include_runtime_reminders=False,
        ),
    )

    await runtime.run("Inspect this.", session_id="session-instruction-skill")

    messages = provider.requests[0].provider_request.messages
    assert [message.role for message in messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    assert messages[0].text.startswith("Instructions from:")
    assert "Project instructions." in messages[0].text
    assert "<available_skills>" in messages[1].text
    assert messages[2].text.startswith('<skill_content name="review-pr">')
    assert messages[3].text == "Inspect this."


def test_instruction_import_does_not_load_legacy_modules():
    code = """
import json
import sys

import efp_runtime.instructions

blocked = [
    "src.sessions",
    "src.agents.core",
    "src.agents.skill_runtime",
    "src.agents.skill_mode",
    "src.runtime",
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
    root: Path,
    name: str,
    *,
    description: str = "Loads skill context",
    content: str = "# Skill\nUse this context.",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir


def _tool_call(call_id: str, tool_name: str) -> dict[str, object]:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": "{}",
        },
    }
