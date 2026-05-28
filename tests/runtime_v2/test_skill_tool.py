import pytest

from efp_runtime.context import render_tool_schemas
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.skills.context import SkillContextBuilder
from efp_runtime.skills.discovery import (
    SkillDiscovery,
    default_skill_directories,
    discover_skills,
)
from efp_runtime.skills.tool import build_skill_list_tool, build_skill_tool
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
        "name: triage\ndescription: Triage issues\nlicense: Apache-2.0\n\n# Triage\n",
        encoding="utf-8",
    )

    skills = discover_skills([tmp_path])

    assert [skill.name for skill in skills] == ["review-pr", "triage"]
    assert skills[0].description == "Review pull requests"
    assert skills[1].content == "# Triage"
    assert skills[1].metadata["license"] == "Apache-2.0"


def test_default_skill_directories_include_opencode_skill_before_plural(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    opencode_skill = tmp_path / ".opencode" / "skill"
    opencode_skills = tmp_path / ".opencode" / "skills"
    opencode_skill.mkdir(parents=True)
    opencode_skills.mkdir(parents=True)

    assert default_skill_directories(tmp_path) == [
        opencode_skill.resolve(),
        opencode_skills.resolve(),
    ]
    assert default_skill_directories(tmp_path, include_defaults=False) == []


def test_default_skill_directories_include_global_before_project_defaults(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    global_claude = home / ".claude" / "skills"
    global_agents = home / ".agents" / "skills"
    global_opencode_skill = home / ".config" / "opencode" / "skill"
    global_opencode_skills = home / ".config" / "opencode" / "skills"
    opencode_skill = workspace / ".opencode" / "skill"
    opencode_skills = workspace / ".opencode" / "skills"
    claude_skills = workspace / ".claude" / "skills"
    agents_skills = workspace / ".agents" / "skills"
    for directory in (
        global_claude,
        global_agents,
        global_opencode_skill,
        global_opencode_skills,
        opencode_skill,
        opencode_skills,
        claude_skills,
        agents_skills,
    ):
        directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    assert default_skill_directories(workspace) == [
        global_claude.resolve(),
        global_agents.resolve(),
        global_opencode_skill.resolve(),
        global_opencode_skills.resolve(),
        claude_skills.resolve(),
        agents_skills.resolve(),
        opencode_skill.resolve(),
        opencode_skills.resolve(),
    ]


def test_skill_discovery_loads_from_global_opencode_skill_directories(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    global_opencode_skill = home / ".config" / "opencode" / "skill"
    global_opencode_skills = home / ".config" / "opencode" / "skills"
    global_opencode_skill.mkdir(parents=True)
    global_opencode_skills.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    singular = _write_skill(
        global_opencode_skill,
        "global-singular",
        description="Global opencode singular skill",
    )
    plural = _write_skill(
        global_opencode_skills,
        "global-plural",
        description="Global opencode plural skill",
    )

    discovery = SkillDiscovery(default_skill_directories(workspace))

    singular_skill = discovery.get("global-singular")
    plural_skill = discovery.get("global-plural")
    assert singular_skill is not None
    assert singular_skill.root == singular
    assert singular_skill.description == "Global opencode singular skill"
    assert plural_skill is not None
    assert plural_skill.root == plural
    assert plural_skill.description == "Global opencode plural skill"


def test_skill_discovery_loads_from_default_opencode_skill_directory(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    opencode_skill = tmp_path / ".opencode" / "skill"
    opencode_skill.mkdir(parents=True)
    skill_dir = _write_skill(
        opencode_skill,
        "local-skill",
        description="Local opencode skill",
    )

    discovery = SkillDiscovery(default_skill_directories(tmp_path))
    skill = discovery.get("local-skill")

    assert skill is not None
    assert skill.root == skill_dir
    assert skill.description == "Local opencode skill"


def test_workspace_default_skill_overrides_same_name_global(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    global_root = home / ".claude" / "skills"
    project_root = workspace / ".opencode" / "skill"
    global_root.mkdir(parents=True)
    project_root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _write_skill(global_root, "shared-skill", content="# Global")
    project_skill = _write_skill(project_root, "shared-skill", content="# Project")

    skill = SkillDiscovery(default_skill_directories(workspace)).get("SHARED-SKILL")

    assert skill is not None
    assert skill.root == project_skill
    assert skill.content == "# Project"


def test_duplicate_skill_names_use_later_configured_directory(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_skill(first, "shared-skill", content="# First")
    winner = _write_skill(second, "Shared-Skill", content="# Second")

    skills = discover_skills([first, second])
    skill = SkillDiscovery([first, second]).get("shared-skill")

    assert [item.name for item in skills] == ["Shared-Skill"]
    assert skills[0].root == winner
    assert skill is not None
    assert skill.root == winner
    assert skill.content == "# Second"


def test_duplicate_skill_names_within_directory_use_stable_path_order(tmp_path):
    old_root = tmp_path / "01-old"
    new_root = tmp_path / "02-new"
    old_root.mkdir()
    new_root.mkdir()
    _write_skill(old_root, "shared-skill", content="# Old")
    winner = _write_skill(new_root, "shared-skill", content="# New")

    skills = discover_skills([tmp_path])

    assert [item.name for item in skills] == ["shared-skill"]
    assert skills[0].root == winner
    assert skills[0].content == "# New"


def test_skill_tool_description_lists_available_skill_names_and_descriptions(tmp_path):
    _write_skill(
        tmp_path,
        "safe-skill",
        description="Loads <context> & references safely",
    )
    no_description = tmp_path / "no-description"
    no_description.mkdir()
    (no_description / "SKILL.md").write_text("# No Description\n", encoding="utf-8")

    tool = build_skill_tool(SkillDiscovery([tmp_path]))

    assert tool.description.startswith("Load a specialized skill")
    assert "skill({name})" in tool.description
    assert "<available_skills>" in tool.description
    assert "<skill>" in tool.description
    assert "<name>safe-skill</name>" in tool.description
    assert (
        "<description>Loads &lt;context&gt; &amp; references safely</description>"
        in tool.description
    )
    assert "<name>no-description</name>" in tool.description

    empty_tool = build_skill_tool(SkillDiscovery([]))

    assert empty_tool.description.endswith(
        "<available_skills>\n"
        "  <no_skills>No skills available.</no_skills>\n"
        "</available_skills>"
    )


def test_skill_tool_description_hides_denied_skills_by_subject_permission(tmp_path):
    _write_skill(tmp_path, "internal-docs", description="Internal docs")
    _write_skill(tmp_path, "public-docs", description="Public docs")

    tool = build_skill_tool(
        SkillDiscovery([tmp_path]),
        tool_permissions={"skill": {"*": "allow", "internal-*": "deny"}},
    )

    assert "<name>public-docs</name>" in tool.description
    assert "<description>Public docs</description>" in tool.description
    assert "internal-docs" not in tool.description
    assert "Internal docs" not in tool.description


def test_skill_tool_description_keeps_ask_skills_visible(tmp_path):
    _write_skill(tmp_path, "experimental-docs", description="Experimental docs")

    tool = build_skill_tool(
        SkillDiscovery([tmp_path]),
        tool_permissions={"skill": {"*": "allow", "experimental-*": "ask"}},
    )

    assert "<name>experimental-docs</name>" in tool.description
    assert "<description>Experimental docs</description>" in tool.description


def test_skill_tool_permission_subject_metadata(tmp_path):
    _write_skill(tmp_path, "safe-skill")
    discovery = SkillDiscovery([tmp_path])

    skill_tool = build_skill_tool(discovery)
    skill_list_tool = build_skill_list_tool(discovery)

    assert skill_tool.permission.category == "skill"
    assert skill_tool.permission.data["subject_arg"] == "name"
    assert skill_list_tool.permission.category == "skill"
    assert "subject_arg" not in skill_list_tool.permission.data


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
    assert "# Skill: safe-skill" in result.content
    assert f"Base directory for this skill: {skill_dir.resolve().as_uri()}/" in result.content
    assert (
        "Relative paths in this skill (e.g., scripts/, reference/) are relative "
        "to this base directory."
    ) in result.content
    assert "Note: file list is sampled." in result.content
    assert "<skill_files>" in result.content
    assert f"<file>{refs_dir / 'guide.md'}</file>" in result.content
    assert f"<file>{skill_dir / 'side_effect.py'}</file>" in result.content
    assert f"<file>{skill_dir / 'SKILL.md'}</file>" not in result.content
    assert "Reference details" in result.content
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
async def test_skill_tool_file_list_is_sampled_and_stable_without_skill_file(tmp_path):
    skill_dir = _write_skill(tmp_path, "sampled-skill")
    for index in range(12):
        (skill_dir / f"{index:02}.md").write_text(
            f"sidecar {index}",
            encoding="utf-8",
        )

    runtime = ToolRuntime(ToolRegistry([build_skill_tool(SkillDiscovery([tmp_path]))]))

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="skill", args={"name": "sampled-skill"})
    )

    file_lines = [
        line for line in result.content.splitlines() if line.startswith("<file>")
    ]
    assert file_lines == [
        f"<file>{skill_dir / f'{index:02}.md'}</file>" for index in range(10)
    ]
    assert f"<file>{skill_dir / '10.md'}</file>" not in result.content
    assert f"<file>{skill_dir / 'SKILL.md'}</file>" not in result.content


@pytest.mark.asyncio
async def test_active_skill_context_and_skill_tool_share_rendered_shape(tmp_path):
    skill_dir = _write_skill(tmp_path, "shared-skill")
    (skill_dir / "guide.md").write_text("Guide", encoding="utf-8")
    discovery = SkillDiscovery([tmp_path])
    active_context = SkillContextBuilder(discovery).build_messages(["shared-skill"])[0]
    runtime = ToolRuntime(ToolRegistry([build_skill_tool(discovery)]))

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="skill", args={"name": "shared-skill"})
    )

    assert result.content == active_context.parts[0].text


@pytest.mark.asyncio
async def test_skill_tool_reports_unknown_skill_as_tool_error(tmp_path):
    _write_skill(tmp_path, "known-skill")
    runtime = ToolRuntime(ToolRegistry([build_skill_tool([tmp_path])]))

    result = await runtime.execute(ToolCall(id="call-1", tool_id="skill", args={"name": "missing"}))

    assert result.status == "error"
    assert "Unknown skill: missing" in result.error
    assert "Available skills: known-skill" in result.error


@pytest.mark.asyncio
async def test_skill_tool_unknown_skill_error_lists_only_visible_skills(tmp_path):
    _write_skill(tmp_path, "internal-docs")
    _write_skill(tmp_path, "public-docs")
    runtime = ToolRuntime(
        ToolRegistry(
            [
                build_skill_tool(
                    SkillDiscovery([tmp_path]),
                    tool_permissions={"skill": {"*": "allow", "internal-*": "deny"}},
                )
            ]
        )
    )

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="skill", args={"name": "missing"})
    )

    assert result.status == "error"
    assert "Unknown skill: missing" in result.error
    assert "Available skills: public-docs" in result.error
    assert "internal-docs" not in result.error


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
    assert "<name>safe-skill</name>" in skill_tool.description
    assert "<description>Loads context safely</description>" in skill_tool.description
    schemas = render_tool_schemas(registry.list())
    skill_schema = next(schema for schema in schemas if schema.id == "skill")
    assert "<name>safe-skill</name>" in skill_schema.description
    assert "<description>Loads context safely</description>" in skill_schema.description


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
    assert "<name>safe-skill</name>" in skill_schema.description
    assert "<description>Loads context safely</description>" in skill_schema.description


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
    assert "<available_skills>" in provider.requests[0].provider_request.messages[0].text
    assert provider.requests[0].provider_request.messages[1].text.startswith(
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


@pytest.mark.asyncio
async def test_agent_runtime_skill_metadata_counts_only_visible_skills(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "internal-docs", description="Internal docs")
    _write_skill(skills_dir, "public-docs", description="Public docs")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            tool_permissions={"skill": {"*": "allow", "internal-*": "deny"}},
            max_iterations=1,
        ),
    )

    result = await runtime.run("Use tools if needed.", session_id="session-visible-count")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.metadata["available_skill_count"] == 1
    assert request.provider_request.metadata["available_skill_count"] == 1
    skill_schema = next(
        schema for schema in request.provider_request.tools if schema.id == "skill"
    )
    assert "<name>public-docs</name>" in skill_schema.description
    assert "internal-docs" not in skill_schema.description


@pytest.mark.asyncio
async def test_agent_runtime_filters_denied_active_skill_context(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "internal-docs", description="Internal docs")
    _write_skill(skills_dir, "public-docs", description="Public docs")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            active_skills=["internal-docs", "public-docs"],
            tool_permissions={"skill": {"*": "allow", "internal-*": "deny"}},
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    result = await runtime.run("Use configured skills.", session_id="session-active-visible")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    texts = [message.text for message in request.provider_request.messages]
    assert any('<skill_content name="public-docs">' in text for text in texts)
    assert all("internal-docs" not in text for text in texts)
    assert request.metadata["active_skills"] == ["public-docs"]
    assert request.metadata["active_skill_count"] == 1
    assert request.provider_request.metadata["active_skills"] == ["public-docs"]


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
