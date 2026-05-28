import pytest

from efp_runtime.models import ToolCall
from efp_runtime.skills.discovery import SkillDiscovery, discover_skills
from efp_runtime.skills.tool import build_skill_tool
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


def test_discovers_uppercase_and_lowercase_skill_files(tmp_path):
    first = tmp_path / "review"
    first.mkdir()
    (first / "SKILL.md").write_text(
        "---\nname: review-pr\ndescription: Review pull requests\n---\n# Review\n",
        encoding="utf-8",
    )

    second = tmp_path / "triage"
    second.mkdir()
    (second / "skill.md").write_text(
        "name: triage\ndescription: Triage issues\n\n# Triage\n",
        encoding="utf-8",
    )

    skills = discover_skills([tmp_path])

    assert [skill.name for skill in skills] == ["review-pr", "triage"]
    assert skills[0].description == "Review pull requests"
    assert skills[1].content == "# Triage"


@pytest.mark.asyncio
async def test_skill_tool_returns_skill_content_and_sidecar_context_without_python_execution(tmp_path):
    skill_dir = tmp_path / "safe-skill"
    refs_dir = skill_dir / "references"
    refs_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: safe-skill\ndescription: Loads context safely\n---\n"
        "# Safe Skill\nUse this context.\n",
        encoding="utf-8",
    )
    (refs_dir / "guide.md").write_text("Reference details", encoding="utf-8")
    sentinel = tmp_path / "executed.txt"
    (skill_dir / "side_effect.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    discovery = SkillDiscovery([tmp_path])
    runtime = ToolRuntime(ToolRegistry([build_skill_tool(discovery)]))

    result = await runtime.execute(
        ToolCall(
            id="call-1",
            tool_id="skill",
            args={"name": "safe-skill", "include_sidecar_content": True},
        )
    )

    assert result.status == "success"
    assert result.output["name"] == "safe-skill"
    assert result.output["content"] == "# Safe Skill\nUse this context."
    sidecar_paths = {entry["path"] for entry in result.output["sidecars"]}
    assert sidecar_paths == {"references/guide.md", "side_effect.py"}
    assert any(entry.get("content") == "Reference details" for entry in result.output["sidecars"])
    assert sentinel.exists() is False


@pytest.mark.asyncio
async def test_skill_tool_reports_unknown_skill_as_tool_error(tmp_path):
    runtime = ToolRuntime(ToolRegistry([build_skill_tool([tmp_path])]))

    result = await runtime.execute(ToolCall(id="call-1", tool_id="skill", args={"name": "missing"}))

    assert result.status == "error"
    assert "Unknown skill: missing" in result.error
