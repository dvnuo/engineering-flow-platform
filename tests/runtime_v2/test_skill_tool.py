import pytest

from efp_runtime.context import render_tool_schemas
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.skills.discovery import SkillDiscovery, discover_skills
from efp_runtime.skills.tool import build_skill_tool
from efp_runtime.tools.builtin import create_core_tool_registry
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


def test_skill_tool_description_lists_available_skill_names_and_descriptions(tmp_path):
    _write_skill(tmp_path, "safe-skill", description="Loads context safely")
    no_description = tmp_path / "no-description"
    no_description.mkdir()
    (no_description / "SKILL.md").write_text("# No Description\n", encoding="utf-8")

    tool = build_skill_tool(SkillDiscovery([tmp_path]))

    assert tool.description.startswith("Load a specialized skill")
    assert "Available skills:" in tool.description
    assert "- safe-skill: Loads context safely" in tool.description
    assert "- no-description" in tool.description

    empty_tool = build_skill_tool(SkillDiscovery([]))

    assert "No skills available." in empty_tool.description


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
    assert result.content.startswith('<skill_content name="safe-skill">')
    assert "Base directory" in result.content
    assert "<skill_files>" in result.content
    assert "- references/guide.md" in result.content
    assert "- side_effect.py" in result.content
    assert result.output["name"] == "safe-skill"
    assert result.output["description"] == "Loads context safely"
    assert result.output["skill_file"] == str(skill_dir / "SKILL.md")
    assert result.output["content"] == "# Safe Skill\nUse this context."
    sidecar_paths = {entry["path"] for entry in result.output["sidecars"]}
    assert sidecar_paths == {"references/guide.md", "side_effect.py"}
    assert any(entry.get("content") == "Reference details" for entry in result.output["sidecars"])
    assert result.output["metadata"]["name"] == "safe-skill"
    assert result.output["metadata"]["skill_file"] == str(skill_dir / "SKILL.md")
    assert result.output["metadata"]["sidecar_count"] == 2
    assert result.metadata["name"] == "safe-skill"
    assert result.metadata["skill_file"] == str(skill_dir / "SKILL.md")
    assert result.metadata["sidecar_count"] == 2
    assert sentinel.exists() is False


@pytest.mark.asyncio
async def test_skill_tool_reports_unknown_skill_as_tool_error(tmp_path):
    _write_skill(tmp_path, "known-skill")
    runtime = ToolRuntime(ToolRegistry([build_skill_tool([tmp_path])]))

    result = await runtime.execute(ToolCall(id="call-1", tool_id="skill", args={"name": "missing"}))

    assert result.status == "error"
    assert "Unknown skill: missing" in result.error
    assert "Available skills: known-skill" in result.error


def test_core_registry_does_not_include_skill_tool_by_default(tmp_path):
    registry = create_core_tool_registry(tmp_path)

    assert "skill" not in registry.ids()


def test_core_registry_can_include_skill_tool_with_provider_schema_description(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "safe-skill", description="Loads context safely")

    registry = create_core_tool_registry(tmp_path, skill_directories=[skills_dir])

    assert "skill" in registry.ids()
    skill_tool = registry.require("skill")
    assert "- safe-skill: Loads context safely" in skill_tool.description
    schemas = render_tool_schemas(registry.list())
    skill_schema = next(schema for schema in schemas if schema.id == "skill")
    assert "- safe-skill: Loads context safely" in skill_schema.description


@pytest.mark.asyncio
async def test_agent_runtime_default_provider_request_tools_include_skill(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "safe-skill", description="Loads context safely")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            max_iterations=1,
        ),
    )

    result = await runtime.run("Use tools if needed.", session_id="session-skill-tools")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert "skill" in [schema.id for schema in request.provider_request.tools]
    skill_schema = next(schema for schema in request.provider_request.tools if schema.id == "skill")
    assert "- safe-skill: Loads context safely" in skill_schema.description


@pytest.mark.asyncio
async def test_active_skill_and_skill_tool_coexist_without_active_skill_pollution(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "active-skill", description="Always active")
    _write_skill(skills_dir, "safe-skill", description="Loaded by tool")
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call-skill",
                        "type": "function",
                        "function": {
                            "name": "skill",
                            "arguments": '{"name": "safe-skill"}',
                        },
                    }
                ]
            },
            {"content": "Done."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            active_skills=["active-skill"],
            max_iterations=2,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    result = await runtime.run("Use the extra skill.", session_id="session-active-and-tool")

    assert result.status == LoopStatus.COMPLETED
    assert runtime.active_skills == ["active-skill"]
    assert provider.requests[0].provider_request.messages[0].text.startswith(
        '<skill_content name="active-skill">'
    )
    assert "skill" in [schema.id for schema in provider.requests[0].provider_request.tools]
    second_request = provider.requests[1]
    assert second_request.metadata["active_skills"] == ["active-skill"]
    tool_results = [
        result
        for message in second_request.provider_request.messages
        for result in message.tool_results
    ]
    assert len(tool_results) == 1
    assert tool_results[0].tool_name == "skill"
    assert '<skill_content name="safe-skill">' in tool_results[0].content


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
