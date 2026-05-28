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


ROOT = Path(__file__).resolve().parents[2]


def test_builder_loads_workspace_default_files_in_stable_order(tmp_path: Path):
    (tmp_path / "CONTEXT.md").write_text("Context instructions.", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Agent instructions.", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Claude instructions.", encoding="utf-8")

    messages = InstructionContextBuilder(workspace_root=tmp_path).build_messages()

    assert [message.role for message in messages] == [
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
        MessageRole.SYSTEM,
    ]
    assert [message.metadata["path"] for message in messages] == [
        str((tmp_path / "AGENTS.md").resolve()),
        str((tmp_path / "CLAUDE.md").resolve()),
        str((tmp_path / "CONTEXT.md").resolve()),
    ]
    assert [message.parts[0].text.splitlines()[0] for message in messages] == [
        f"Instructions from: {(tmp_path / 'AGENTS.md').resolve()}",
        f"Instructions from: {(tmp_path / 'CLAUDE.md').resolve()}",
        f"Instructions from: {(tmp_path / 'CONTEXT.md').resolve()}",
    ]
    assert messages[0].metadata == {
        "kind": "instruction_context",
        "source": "file",
        "path": str((tmp_path / "AGENTS.md").resolve()),
        "truncated": False,
        "original_chars": len("Agent instructions."),
    }
    assert messages[0].parts[0].metadata == messages[0].metadata


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
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
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
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
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
        ),
    )

    await runtime.run("Inspect this.", session_id="session-instruction-skill")

    messages = provider.requests[0].provider_request.messages
    assert [message.role for message in messages] == ["system", "system", "user"]
    assert messages[0].text.startswith("Instructions from:")
    assert "Project instructions." in messages[0].text
    assert messages[1].text.startswith('<skill_content name="review-pr">')
    assert messages[2].text == "Inspect this."


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
