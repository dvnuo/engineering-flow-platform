import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.context import render_messages
from efp_runtime.models import MessageRole
from efp_runtime.skills.commands import SkillCommandResult, parse_skill_commands
from efp_runtime.skills.context import SkillContextBuilder, skill_package_to_system_message
from efp_runtime.skills.discovery import SkillDiscovery


ROOT = Path(__file__).resolve().parents[2]


def test_skill_context_builder_generates_system_message_with_metadata(tmp_path):
    skill_dir = _write_skill(
        tmp_path,
        "review-pr",
        description="Review pull requests",
        content="# Review\nCheck diffs and tests.",
    )
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide.md").write_text("Use focused findings.", encoding="utf-8")

    messages = SkillContextBuilder(SkillDiscovery([tmp_path])).build_messages(["review-pr"])

    assert len(messages) == 1
    message = messages[0]
    assert message.role is MessageRole.SYSTEM
    assert message.metadata["kind"] == "skill_context"
    assert message.metadata["skill_name"] == "review-pr"
    assert message.metadata["skill_file"] == str(skill_dir / "SKILL.md")
    part = message.parts[0]
    assert part.metadata == message.metadata
    assert "Skill: review-pr" in part.text
    assert "Description: Review pull requests" in part.text
    assert "# Review\nCheck diffs and tests." in part.text
    assert "- references/guide.md" in part.text


def test_skill_context_sidecars_default_to_metadata_without_reading_content(tmp_path):
    skill_dir = _write_skill(tmp_path, "safe-skill")
    sidecar = skill_dir / "side_effect.py"
    sidecar.write_text("raise RuntimeError('must not run')\n", encoding="utf-8")

    message = skill_package_to_system_message(SkillDiscovery([tmp_path]).get("safe-skill"))

    text = message.parts[0].text
    assert "- side_effect.py" in text
    assert "bytes" in text
    assert "raise RuntimeError" not in text


def test_skill_context_can_include_truncated_sidecar_text(tmp_path):
    skill_dir = _write_skill(tmp_path, "docs-skill")
    (skill_dir / "guide.md").write_text("abcdef", encoding="utf-8")

    message = skill_package_to_system_message(
        SkillDiscovery([tmp_path]).get("docs-skill"),
        include_sidecar_content=True,
        max_sidecar_chars=3,
    )

    text = message.parts[0].text
    assert "- guide.md" in text
    assert "truncated to 3 of 6 chars" in text
    assert "  abc" in text
    assert "abcdef" not in text


def test_skill_context_unknown_skill_reports_available_names(tmp_path):
    _write_skill(tmp_path, "known-skill")
    builder = SkillContextBuilder(SkillDiscovery([tmp_path]))

    with pytest.raises(KeyError) as error:
        builder.build_messages(["missing"])

    error_text = str(error.value)
    assert "Unknown skill: missing" in error_text
    assert "Available skills: known-skill" in error_text


def test_parse_skill_name_command_removes_command_line():
    result = parse_skill_commands("/skill review-pr\nPlease inspect the diff.")

    assert result == SkillCommandResult(
        cleaned_text="Please inspect the diff.",
        add=["review-pr"],
        clear=False,
    )


def test_parse_skill_clear_command_removes_command_line():
    result = parse_skill_commands("Before\n/skill clear\nAfter\n")

    assert result.cleaned_text == "Before\nAfter\n"
    assert result.add == []
    assert result.clear is True


def test_parse_skill_commands_handles_multiline_mixed_text():
    result = parse_skill_commands(
        "First line\n"
        "  /skill   review-pr  \n"
        "/skill docs-skill\n"
        "Middle /skill literal\n"
        "/skill clear\n"
        "Last line\n"
    )

    assert result.cleaned_text == "First line\nMiddle /skill literal\nLast line\n"
    assert result.add == ["review-pr", "docs-skill"]
    assert result.clear is True


def test_render_messages_preserves_skill_context_metadata(tmp_path):
    _write_skill(tmp_path, "render-skill")
    message = SkillContextBuilder(SkillDiscovery([tmp_path])).build_messages(["render-skill"])[0]

    rendered = render_messages([message])

    assert len(rendered) == 1
    assert rendered[0].role == "system"
    assert "Skill: render-skill" in rendered[0].text
    part_metadata = rendered[0].parts[0].metadata["part_metadata"]
    assert part_metadata["kind"] == "skill_context"
    assert part_metadata["skill_name"] == "render-skill"
    assert part_metadata["skill_file"].endswith("SKILL.md")


def test_skill_context_and_commands_do_not_import_legacy_runtime_modules():
    code = """
import json
import sys

import efp_runtime.skills.context
import efp_runtime.skills.commands

blocked = [
    "src.agents.core",
    "src.agents.skill_runtime",
    "src.agents.skill_mode",
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
    tmp_path,
    name,
    *,
    description="Loads skill context",
    content="# Skill\nUse this context.",
):
    skill_dir = tmp_path / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir
