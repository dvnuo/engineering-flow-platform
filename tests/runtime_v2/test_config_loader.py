from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.agents import DEFAULT_AGENT_PROFILE_NAMES
from efp_runtime.config_loader import (
    find_runtime_config_files,
    load_runtime_config,
    resolve_runtime_workspace_root,
)
from efp_runtime.permissions import normalize_agent_permission_overlay
from efp_runtime.skills.discovery import SkillDiscovery


ROOT = Path(__file__).resolve().parents[2]


def test_default_file_lookup_and_merge_order(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "permissions": {"edit": "deny"},
            "disabledTools": ["read_file"],
            "instructions": ["base.md"],
            "activeSkills": ["base"],
            "runtime_mode": "build",
        },
    )
    nested = tmp_path / ".opencode" / "config.json"
    _write_json(
        nested,
        {
            "permission": {"edit": "allow", "bash": "allow"},
            "disabled_tools": ["write_file", "read_file"],
            "instructions": ["override.md", "base.md"],
            "active_skills": ["base", "review"],
            "runtime": {"mode": "plan"},
        },
    )

    found = find_runtime_config_files(tmp_path)
    result = load_runtime_config(tmp_path)

    assert found == [tmp_path / "opencode.json", nested]
    assert result.loaded_paths == found
    assert result.raw["runtime_mode"] == "build"
    assert result.config.runtime_mode == "plan"
    assert result.config.tool_permissions == {"edit": "allow", "bash": "allow"}
    assert result.config.disabled_tools == ["read_file", "write_file"]
    assert result.config.instruction_paths == [
        (tmp_path / "base.md").resolve(),
        (tmp_path / "override.md").resolve(),
    ]
    assert result.config.active_skills == ["base", "review"]


def test_load_runtime_config_resolves_parent_opencode_json_from_nested_dir(
    tmp_path: Path,
):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    _write_json(
        project / "opencode.json",
        {
            "runtime_mode": "plan",
            "instructions": ["README.md"],
        },
    )

    found = find_runtime_config_files(nested)
    result = load_runtime_config(nested)

    assert found == [(project / "opencode.json").resolve()]
    assert result.loaded_paths == found
    assert result.config.workspace_root == project.resolve()
    assert result.config.runtime_mode == "plan"
    assert result.config.instruction_paths == [(project / "README.md").resolve()]


def test_load_runtime_config_resolves_parent_dot_opencode_config_from_nested_dir(
    tmp_path: Path,
):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    config_path = project / ".opencode" / "config.json"
    _write_json(config_path, {"runtime": {"mode": "plan"}})

    result = load_runtime_config(nested)

    assert result.loaded_paths == [config_path.resolve()]
    assert result.config.workspace_root == project.resolve()
    assert result.config.runtime_mode == "plan"


def test_parent_default_commands_marker_resolves_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    commands = project / ".opencode" / "commands"
    commands.mkdir(parents=True)
    (commands / "audit.md").write_text("Audit nested work.", encoding="utf-8")

    result = load_runtime_config(nested)

    assert result.loaded_paths == []
    assert result.config.workspace_root == project.resolve()
    assert result.config.command_directories == [commands.resolve()]
    assert result.command_registry is not None
    assert result.command_registry.get("audit").content == "Audit nested work."


@pytest.mark.parametrize("default_name", ["tool", "tools"])
def test_parent_default_tool_marker_resolves_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default_name: str,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    tools = project / ".opencode" / default_name
    tools.mkdir(parents=True)
    (tools / "hello.py").write_text(
        "TOOL = {'description': 'Hello', 'execute': lambda args, context: 'hi'}\n",
        encoding="utf-8",
    )

    result = load_runtime_config(nested)

    assert result.loaded_paths == []
    assert result.config.workspace_root == project.resolve()
    assert result.config.local_tool_directories == [tools.resolve()]


@pytest.mark.parametrize("default_name", ["skill", "skills"])
def test_parent_default_skill_marker_resolves_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default_name: str,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    skills = project / ".opencode" / default_name
    _write_skill(skills, "project-skill", content="# Project skill")

    result = load_runtime_config(nested)
    skill = SkillDiscovery(result.config.skill_directories).get("project-skill")

    assert result.loaded_paths == []
    assert result.config.workspace_root == project.resolve()
    assert result.config.skill_directories == [skills.resolve()]
    assert skill is not None
    assert skill.skill_file == skills / "project-skill" / "SKILL.md"


def test_loader_exposes_default_project_skill_as_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    skills = tmp_path / ".opencode" / "skill"
    skill_dir = _write_skill(skills, "project-skill", content="# Project skill")

    result = load_runtime_config(tmp_path)

    assert result.command_registry is not None
    infos = {info.name: info for info in result.command_registry.list()}
    assert result.config.skill_directories == [skills.resolve()]
    assert infos["project-skill"].source == "skill"
    assert infos["project-skill"].command_file == skill_dir / "SKILL.md"
    assert result.command_registry.get("project-skill").source == "skill"
    assert result.command_registry.get("project-skill").content == "# Project skill"


@pytest.mark.parametrize("marker", [".claude/skills", ".agents/skills"])
def test_parent_compatibility_skill_marker_resolves_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    skills = project / marker
    _write_skill(skills, "project-skill", content="# Project skill")

    result = load_runtime_config(nested)
    skill = SkillDiscovery(result.config.skill_directories).get("project-skill")

    assert resolve_runtime_workspace_root(nested) == project.resolve()
    assert result.loaded_paths == []
    assert result.config.workspace_root == project.resolve()
    assert result.config.skill_directories == [skills.resolve()]
    assert skill is not None
    assert skill.skill_file == skills / "project-skill" / "SKILL.md"


@pytest.mark.parametrize("marker", [".claude/skills", ".agents/skills"])
def test_home_compatibility_skill_marker_does_not_resolve_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker: str,
):
    home = tmp_path / "home"
    global_skills = home / marker
    nested = home / "workspace" / "unmarked" / "src" / "pkg"
    global_skills.mkdir(parents=True)
    nested.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    result = load_runtime_config(nested)

    assert resolve_runtime_workspace_root(nested) == nested.resolve()
    assert result.config.workspace_root == nested.resolve()
    assert result.config.workspace_root != home.resolve()
    assert global_skills.resolve() in result.config.skill_directories


def test_nested_project_marker_wins_over_parent_marker(tmp_path: Path):
    outer = tmp_path / "outer"
    inner = outer / "packages" / "app"
    nested = inner / "src" / "pkg"
    nested.mkdir(parents=True)
    inner_config = inner / ".opencode" / "config.json"
    _write_json(outer / "opencode.json", {"runtime_mode": "build"})
    _write_json(inner_config, {"runtime_mode": "plan"})

    result = load_runtime_config(nested)

    assert resolve_runtime_workspace_root(nested) == inner.resolve()
    assert result.loaded_paths == [inner_config.resolve()]
    assert result.config.workspace_root == inner.resolve()
    assert result.config.runtime_mode == "plan"


def test_include_defaults_false_does_not_resolve_parent_marker(tmp_path: Path):
    project = tmp_path / "project"
    nested = project / "src" / "pkg"
    nested.mkdir(parents=True)
    _write_json(project / "opencode.json", {"runtime_mode": "plan"})

    result = load_runtime_config(nested, include_defaults=False)

    assert (
        resolve_runtime_workspace_root(nested, include_defaults=False)
        == nested.resolve()
    )
    assert result.loaded_paths == []
    assert result.config.workspace_root == nested.resolve()
    assert result.config.runtime_mode == "build"
    assert result.config.command_directories == []
    assert result.config.skill_directories == []
    assert result.config.local_tool_directories == []


def test_jsonc_comments_trailing_commas_and_comment_like_strings(tmp_path: Path):
    path = tmp_path / "opencode.jsonc"
    path.write_text(
        """
        {
          // line comment
          "systemPrompt": [
            "Keep https://example.test//literal",
          ],
          "instructions": [
            {"text": "Do not strip // inside strings"},
          ],
          "enabledTools": ["read_file",],
          /* block comment */
        }
        """,
        encoding="utf-8",
    )

    result = load_runtime_config(tmp_path)

    assert result.config.system_prompt_texts == ["Keep https://example.test//literal"]
    assert result.config.instruction_texts == ["Do not strip // inside strings"]
    assert result.config.enabled_tools == ["read_file"]


def test_config_variable_substitutes_env_in_json_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("EFP_TEST_MODEL", 'local/"model"\nnext')
    _write_json(tmp_path / "opencode.json", {"model": "prefix-{env:EFP_TEST_MODEL}"})

    result = load_runtime_config(tmp_path)

    expected = 'prefix-local/"model"\nnext'
    assert result.raw["model"] == expected
    assert result.metadata["raw_config"]["model"] == expected
    assert result.metadata["unconsumed_config"]["model"] == expected


def test_config_variable_missing_env_becomes_empty_string(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("EFP_TEST_MISSING_MODEL", raising=False)
    _write_json(tmp_path / "opencode.json", {"model": "{env:EFP_TEST_MISSING_MODEL}"})

    result = load_runtime_config(tmp_path)

    assert result.raw["model"] == ""
    assert result.metadata["raw_config"]["model"] == ""


def test_config_variable_reads_file_relative_to_config_directory(tmp_path: Path):
    config_path = tmp_path / ".opencode" / "config.jsonc"
    secret_path = config_path.parent / "secret.txt"
    secret_path.parent.mkdir(parents=True)
    secret_path.write_text("  local secret\n", encoding="utf-8")
    _write_json(config_path, {"systemPrompt": ["{file:secret.txt}"]})

    result = load_runtime_config(tmp_path)

    assert result.raw["systemPrompt"] == ["local secret"]
    assert result.config.system_prompt_texts == ["local secret"]


def test_config_variable_reads_file_from_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / "secret.txt").write_text("home secret\n", encoding="utf-8")
    _write_json(tmp_path / "opencode.json", {"systemPrompt": ["{file:~/secret.txt}"]})

    result = load_runtime_config(tmp_path)

    assert result.raw["systemPrompt"] == ["home secret"]
    assert result.config.system_prompt_texts == ["home secret"]


def test_config_variable_json_escapes_file_content_inside_string(tmp_path: Path):
    secret_path = tmp_path / "secret.txt"
    secret_path.write_text('  quote "one"\npath C:\\tmp\n', encoding="utf-8")
    _write_json(tmp_path / "opencode.json", {"systemPrompt": ["{file:secret.txt}"]})

    result = load_runtime_config(tmp_path)

    expected = 'quote "one"\npath C:\\tmp'
    assert result.raw["systemPrompt"] == [expected]
    assert result.config.system_prompt_texts == [expected]


def test_config_variable_missing_file_error_includes_context(tmp_path: Path):
    config_path = tmp_path / ".opencode" / "config.jsonc"
    _write_json(config_path, {"systemPrompt": ["{file:missing.txt}"]})

    with pytest.raises(ValueError) as error:
        load_runtime_config(tmp_path)

    resolved = (config_path.parent / "missing.txt").resolve(strict=False)
    message = str(error.value)
    assert "{file:missing.txt}" in message
    assert str(resolved) in message
    assert str(config_path) in message


def test_config_variable_file_token_in_jsonc_line_comment_is_ignored(tmp_path: Path):
    path = tmp_path / "opencode.jsonc"
    path.write_text(
        """
        {
          "systemPrompt": ["ok"], // {file:missing.txt}
          // "systemPrompt": ["{file:also-missing.txt}"],
        }
        """,
        encoding="utf-8",
    )

    result = load_runtime_config(tmp_path)

    assert result.raw["systemPrompt"] == ["ok"]
    assert result.config.system_prompt_texts == ["ok"]


def test_config_variable_token_outside_json_string_is_not_expanded(tmp_path: Path):
    path = tmp_path / "opencode.jsonc"
    path.write_text('{"model": {file:missing.txt}}', encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_runtime_config(tmp_path)

    message = str(error.value)
    assert "Invalid JSON" in message
    assert "missing.txt" not in message


def test_runtime_config_field_mapping(tmp_path: Path):
    _write_json(
        tmp_path / "custom.json",
        {
            "permission": {"edit": "deny"},
            "enabledTools": ["read_file", "grep"],
            "disabled_tools": ["write_file"],
            "instructions": [
                "docs/AGENTS.md",
                {"path": "docs/extra.md"},
                {"text": "Inline project instruction."},
            ],
            "systemPrompt": ["Base system prompt."],
            "skillDirectories": ["skills", "more-skills"],
            "activeSkills": ["review", "review"],
            "commandDirectories": ["commands", "commands"],
            "toolDirectories": ["tools", "more-tools"],
            "runtime": {"mode": "plan"},
        },
    )

    result = load_runtime_config(tmp_path, paths=["custom.json"], include_defaults=False)
    config = result.config

    assert config.workspace_root == tmp_path.resolve()
    assert config.tool_permissions == {"edit": "deny"}
    assert config.enabled_tools == ["read_file", "grep"]
    assert config.disabled_tools == ["write_file"]
    assert config.instruction_paths == [
        (tmp_path / "docs/AGENTS.md").resolve(),
        (tmp_path / "docs/extra.md").resolve(),
    ]
    assert config.instruction_texts == ["Inline project instruction."]
    assert config.system_prompt_texts == ["Base system prompt."]
    assert config.skill_directories == [
        (tmp_path / "skills").resolve(),
        (tmp_path / "more-skills").resolve(),
    ]
    assert config.active_skills == ["review"]
    assert config.command_directories == [
        (tmp_path / "commands").resolve(),
    ]
    assert config.local_tool_directories == [
        (tmp_path / "tools").resolve(),
        (tmp_path / "more-tools").resolve(),
    ]
    assert config.runtime_mode == "plan"


def test_configured_local_tool_directories_append_after_defaults(tmp_path: Path):
    default_tool = tmp_path / ".opencode" / "tool"
    default_tools = tmp_path / ".opencode" / "tools"
    default_tool.mkdir(parents=True)
    default_tools.mkdir(parents=True)
    _write_json(
        tmp_path / "opencode.json",
        {
            "toolDirectories": ["project-tools", ".opencode/tool"],
            "tool_directories": ["more-tools"],
        },
    )

    result = load_runtime_config(tmp_path)

    assert result.config.local_tool_directories == [
        default_tool.resolve(),
        default_tools.resolve(),
        (tmp_path / "project-tools").resolve(),
        (tmp_path / "more-tools").resolve(),
    ]
    assert "toolDirectories" not in result.metadata["unconsumed_config"]
    assert "tool_directories" not in result.metadata["unconsumed_config"]


def test_model_aware_tool_selection_config_aliases(tmp_path: Path):
    _write_json(
        tmp_path / "camel.json",
        {
            "modelAwareToolSelection": False,
        },
    )
    _write_json(
        tmp_path / "snake.json",
        {
            "runtime": {"model_aware_tool_selection": False},
        },
    )

    camel = load_runtime_config(
        tmp_path,
        paths=["camel.json"],
        include_defaults=False,
    )
    snake = load_runtime_config(
        tmp_path,
        paths=["snake.json"],
        include_defaults=False,
    )

    assert camel.config.model_aware_tool_selection is False
    assert snake.config.model_aware_tool_selection is False
    assert camel.metadata["unconsumed_config"] == {}
    assert snake.metadata["unconsumed_config"] == {}


def test_default_skill_directories_precede_configured_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_claude = home / ".claude" / "skills"
    global_agents = home / ".agents" / "skills"
    global_opencode_skill = home / ".config" / "opencode" / "skill"
    global_opencode_skills = home / ".config" / "opencode" / "skills"
    opencode_skill = tmp_path / ".opencode" / "skill"
    opencode_skills = tmp_path / ".opencode" / "skills"
    claude_skills = tmp_path / ".claude" / "skills"
    agents_skills = tmp_path / ".agents" / "skills"
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
    _write_json(
        tmp_path / "opencode.json",
        {
            "skillDirectories": [
                "skills",
                ".opencode/skills",
            ],
        },
    )

    result = load_runtime_config(tmp_path)

    assert result.config.skill_directories == [
        global_claude.resolve(),
        global_agents.resolve(),
        global_opencode_skill.resolve(),
        global_opencode_skills.resolve(),
        claude_skills.resolve(),
        agents_skills.resolve(),
        opencode_skill.resolve(),
        opencode_skills.resolve(),
        (tmp_path / "skills").resolve(),
    ]


def test_default_skill_directory_order_prefers_project_opencode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_claude = home / ".claude" / "skills"
    global_agents = home / ".agents" / "skills"
    global_opencode_skill = home / ".config" / "opencode" / "skill"
    global_opencode_skills = home / ".config" / "opencode" / "skills"
    project_claude = tmp_path / ".claude" / "skills"
    project_agents = tmp_path / ".agents" / "skills"
    project_opencode_skill = tmp_path / ".opencode" / "skill"
    project_opencode_skills = tmp_path / ".opencode" / "skills"
    for directory in (
        global_claude,
        global_agents,
        global_opencode_skill,
        global_opencode_skills,
        project_claude,
        project_agents,
        project_opencode_skill,
        project_opencode_skills,
    ):
        directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _write_skill(global_claude, "shared-skill", content="# Global external")
    _write_skill(global_opencode_skills, "shared-skill", content="# Global opencode")
    _write_skill(project_agents, "shared-skill", content="# Project external")
    winner = _write_skill(
        project_opencode_skills,
        "shared-skill",
        content="# Project opencode",
    )

    result = load_runtime_config(tmp_path)
    skill = SkillDiscovery(result.config.skill_directories).get("shared-skill")

    assert result.config.skill_directories == [
        global_claude.resolve(),
        global_agents.resolve(),
        global_opencode_skill.resolve(),
        global_opencode_skills.resolve(),
        project_claude.resolve(),
        project_agents.resolve(),
        project_opencode_skill.resolve(),
        project_opencode_skills.resolve(),
    ]
    assert skill is not None
    assert skill.root == winner
    assert skill.content == "# Project opencode"


def test_missing_default_skill_directories_are_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _write_json(
        tmp_path / "opencode.json",
        {
            "skillDirectories": ["skills"],
        },
    )

    result = load_runtime_config(tmp_path)

    assert result.config.skill_directories == [
        (tmp_path / "skills").resolve(),
    ]


def test_include_defaults_false_does_not_add_default_skill_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".agents" / "skills").mkdir(parents=True)
    (home / ".config" / "opencode" / "skill").mkdir(parents=True)
    (home / ".config" / "opencode" / "skills").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (tmp_path / ".claude" / "skills").mkdir(parents=True)
    (tmp_path / ".agents" / "skills").mkdir(parents=True)
    (tmp_path / ".opencode" / "skill").mkdir(parents=True)
    (tmp_path / ".opencode" / "skills").mkdir(parents=True)
    _write_json(
        tmp_path / "custom.json",
        {
            "skillDirectories": ["skills"],
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )

    assert result.config.skill_directories == [
        (tmp_path / "skills").resolve(),
    ]


def test_skills_paths_string_form_resolves_workspace_relative_path(tmp_path: Path):
    _write_json(
        tmp_path / "custom.json",
        {
            "skills": {"paths": "local-skills"},
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )

    assert result.config.skill_directories == [
        (tmp_path / "local-skills").resolve(),
    ]
    assert "skills" not in result.metadata["unconsumed_config"]


def test_skills_paths_list_form_dedupes_with_skill_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    default_skills = tmp_path / ".opencode" / "skill"
    default_skills.mkdir(parents=True)
    _write_json(
        tmp_path / "opencode.json",
        {
            "skillDirectories": ["shared-skills", "configured-skills"],
            "skills": {
                "paths": [
                    "shared-skills",
                    "extra-skills",
                ],
            },
        },
    )

    result = load_runtime_config(tmp_path)

    assert result.config.skill_directories == [
        default_skills.resolve(),
        (tmp_path / "shared-skills").resolve(),
        (tmp_path / "configured-skills").resolve(),
        (tmp_path / "extra-skills").resolve(),
    ]
    assert "skills" not in result.metadata["unconsumed_config"]


def test_configured_skill_directory_overrides_same_name_global_and_project_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_root = home / ".claude" / "skills"
    project_root = tmp_path / ".opencode" / "skill"
    configured_root = tmp_path / "configured-skills"
    for directory in (global_root, project_root, configured_root):
        directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    _write_skill(global_root, "shared-skill", content="# Global")
    _write_skill(project_root, "shared-skill", content="# Project")
    winner = _write_skill(configured_root, "shared-skill", content="# Configured")
    _write_json(
        tmp_path / "opencode.json",
        {
            "skillDirectories": ["configured-skills"],
        },
    )

    result = load_runtime_config(tmp_path)
    skill = SkillDiscovery(result.config.skill_directories).get("shared-skill")

    assert result.config.skill_directories == [
        global_root.resolve(),
        project_root.resolve(),
        configured_root.resolve(),
    ]
    assert skill is not None
    assert skill.root == winner
    assert skill.content == "# Configured"


def test_unsupported_skills_urls_remain_unconsumed_config(tmp_path: Path):
    _write_json(
        tmp_path / "custom.json",
        {
            "skills": {
                "paths": ["local-skills"],
                "urls": ["https://example.test/skills/review"],
            },
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )

    assert result.config.skill_directories == [
        (tmp_path / "local-skills").resolve(),
    ]
    assert result.metadata["unconsumed_config"]["skills"] == {
        "urls": ["https://example.test/skills/review"],
    }


def test_non_object_skills_config_raises_clear_error(tmp_path: Path):
    _write_json(
        tmp_path / "custom.json",
        {
            "skills": ["local-skills"],
        },
    )

    with pytest.raises(ValueError, match="skills must be an object"):
        load_runtime_config(
            tmp_path,
            paths=["custom.json"],
            include_defaults=False,
        )


def test_load_runtime_config_includes_builtin_agents_by_default(tmp_path: Path):
    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert result.loaded_paths == []
    assert registry is not None
    assert set(registry.names()) == set(DEFAULT_AGENT_PROFILE_NAMES)
    assert registry.default_agent == "general"
    assert registry.resolve(None).name == "general"
    assert registry.resolve("general").metadata == {
        "mode": "general",
        "built_in": True,
    }


def test_include_defaults_false_does_not_add_builtin_agents(tmp_path: Path):
    result = load_runtime_config(tmp_path, include_defaults=False)

    assert result.agent_registry is None


def test_default_agent_and_mode_directories_are_loaded(tmp_path: Path):
    _write_text(
        tmp_path / ".opencode" / "agent" / "review.md",
        "Review from singular agent directory.",
    )
    _write_text(
        tmp_path / ".opencode" / "agents" / "debug.md",
        "Debug from plural agent directory.",
    )
    _write_text(
        tmp_path / ".opencode" / "agent" / "nested" / "trace.md",
        "Nested agent files are loaded recursively.",
    )
    _write_text(
        tmp_path / ".opencode" / "mode" / "plan.md",
        "Plan from singular mode directory.",
    )
    _write_text(
        tmp_path / ".opencode" / "modes" / "build.md",
        """
        ---
        mode: subagent
        ---
        Build from plural mode directory.
        """,
    )
    _write_text(
        tmp_path / ".opencode" / "modes" / "nested" / "skip.md",
        "Nested mode files are not loaded by default.",
    )

    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    assert "skip" not in registry.names()
    assert registry.resolve("review").prompt == "Review from singular agent directory."
    assert registry.resolve("debug").prompt == "Debug from plural agent directory."
    assert (
        registry.resolve("trace").prompt
        == "Nested agent files are loaded recursively."
    )

    plan = registry.resolve("plan")
    assert plan.prompt == "Plan from singular mode directory."
    assert plan.metadata["mode"] == "primary"

    build = registry.resolve("build")
    assert build.prompt == "Build from plural mode directory."
    assert build.metadata["mode"] == "primary"


def test_include_defaults_false_skips_default_agent_and_mode_directories(
    tmp_path: Path,
):
    for relative_path in (
        ".opencode/agent/review.md",
        ".opencode/agents/debug.md",
        ".opencode/mode/plan.md",
        ".opencode/modes/build.md",
    ):
        _write_text(tmp_path / relative_path, "Default markdown profile.")

    result = load_runtime_config(tmp_path, include_defaults=False)

    assert result.agent_registry is None


def test_markdown_plan_agent_overrides_builtin_plan(tmp_path: Path):
    _write_text(
        tmp_path / ".opencode" / "agents" / "plan.md",
        """
        ---
        description: Workspace plan
        ---
        Workspace plan prompt.
        """,
    )

    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    assert set(DEFAULT_AGENT_PROFILE_NAMES).issubset(registry.names())
    profile = registry.resolve("plan")
    assert profile.description == "Workspace plan"
    assert profile.prompt == "Workspace plan prompt."
    assert profile.metadata == {"mode": "all"}


def test_config_plan_agent_overrides_markdown_and_builtin_plan(tmp_path: Path):
    _write_text(
        tmp_path / ".opencode" / "agents" / "plan.md",
        """
        ---
        description: Markdown plan
        permission:
          edit: deny
        ---
        Markdown plan prompt.
        """,
    )
    _write_json(
        tmp_path / "opencode.json",
        {
            "agents": {
                "plan": {
                    "description": "Config plan",
                    "prompt": "Config plan prompt.",
                    "permission": {"edit": "allow"},
                },
            },
        },
    )

    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    profile = registry.resolve("plan")
    assert profile.description == "Config plan"
    assert profile.prompt == "Config plan prompt."
    assert profile.metadata == {"permission": {"edit": "allow"}, "mode": "all"}


def test_config_disabled_agent_removes_builtin_by_name(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "agents": {
                "plan": {"disabled": True},
            },
        },
    )

    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    assert "plan" not in registry.names()
    assert registry.resolve(None).name == "general"


def test_builtin_read_focused_agents_carry_permission_metadata(tmp_path: Path):
    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    plan = registry.resolve("plan")
    explore = registry.resolve("explore")
    scout = registry.resolve("scout")
    deny_overlay = {
        "edit": "deny",
        "write_file": "deny",
        "write": "deny",
        "apply_patch": "deny",
        "shell_exec": "deny",
        "bash": "deny",
        "task": "deny",
        "task_cancel": "deny",
    }
    ask_overlay = {tool_id: "ask" for tool_id in deny_overlay}
    assert normalize_agent_permission_overlay(plan.metadata) == deny_overlay
    assert normalize_agent_permission_overlay(scout.metadata) == deny_overlay
    assert normalize_agent_permission_overlay(explore.metadata) == ask_overlay


def test_loader_returns_command_definitions_registry_and_default_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    default_commands = tmp_path / ".opencode" / "commands"
    default_commands.mkdir(parents=True)
    (default_commands / "test.md").write_text("Markdown override.", encoding="utf-8")
    _write_json(
        tmp_path / "opencode.json",
        {
            "command": {
                "test": {
                    "template": "Run tests for $ARGUMENTS",
                    "agent": "build",
                    "model": "provider/model",
                    "subtask": False,
                },
                "review": "Review $1",
            },
            "commandDirectories": ["project-commands"],
        },
    )

    result = load_runtime_config(tmp_path)

    assert [definition.name for definition in result.command_definitions] == [
        "test",
        "review",
    ]
    assert result.command_definitions[0].source == "config"
    assert result.command_definitions[0].agent == "build"
    assert result.command_definitions[0].model == "provider/model"
    assert result.command_definitions[0].subtask is False
    assert result.config.command_directories == [
        default_commands.resolve(),
        (tmp_path / "project-commands").resolve(),
    ]
    assert result.command_registry is not None
    assert result.command_registry.get("test").content == "Markdown override."
    assert result.command_registry.get("review").content == "Review $1"
    assert "command" not in result.metadata["unconsumed_config"]
    assert "commandDirectories" not in result.metadata["unconsumed_config"]


def test_loader_registers_builtin_commands_outside_config_definitions(
    tmp_path: Path,
):
    result = load_runtime_config(tmp_path, include_defaults=False)

    assert result.command_definitions == []
    assert result.command_registry is not None
    init = result.command_registry.get("init")
    review = result.command_registry.get("review")
    assert init is not None
    assert review is not None
    assert init.source == "builtin"
    assert review.source == "builtin"
    assert review.subtask is True
    assert str(tmp_path.resolve()) in init.content


def test_loader_returns_singular_default_command_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    default_commands = tmp_path / ".opencode" / "command"
    default_commands.mkdir(parents=True)
    (default_commands / "test.md").write_text("Singular command.", encoding="utf-8")

    result = load_runtime_config(tmp_path)

    assert result.config.command_directories == [default_commands.resolve()]
    assert result.command_registry is not None
    assert result.command_registry.get("test").content == "Singular command."


def test_loader_orders_global_singular_plural_then_configured_command_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_commands = home / ".config" / "opencode" / "commands"
    singular_commands = tmp_path / ".opencode" / "command"
    plural_commands = tmp_path / ".opencode" / "commands"
    project_commands = tmp_path / "project-commands"
    global_commands.mkdir(parents=True)
    singular_commands.mkdir(parents=True)
    plural_commands.mkdir(parents=True)
    project_commands.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (global_commands / "dup.md").write_text("Global command.", encoding="utf-8")
    (singular_commands / "dup.md").write_text("Singular command.", encoding="utf-8")
    (plural_commands / "dup.md").write_text("Plural command.", encoding="utf-8")
    (project_commands / "dup.md").write_text("Configured command.", encoding="utf-8")
    _write_json(
        tmp_path / "opencode.json",
        {
            "commandDirectories": ["project-commands"],
        },
    )

    result = load_runtime_config(tmp_path)

    assert result.config.command_directories == [
        global_commands.resolve(),
        singular_commands.resolve(),
        plural_commands.resolve(),
        project_commands.resolve(),
    ]
    assert result.command_registry is not None
    assert result.command_registry.get("dup").content == "Configured command."


def test_include_defaults_false_skips_default_command_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_commands = home / ".config" / "opencode" / "commands"
    singular_commands = tmp_path / ".opencode" / "command"
    plural_commands = tmp_path / ".opencode" / "commands"
    project_commands = tmp_path / "project-commands"
    global_commands.mkdir(parents=True)
    singular_commands.mkdir(parents=True)
    plural_commands.mkdir(parents=True)
    project_commands.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (global_commands / "dup.md").write_text("Global command.", encoding="utf-8")
    (singular_commands / "dup.md").write_text("Singular command.", encoding="utf-8")
    (plural_commands / "dup.md").write_text("Plural command.", encoding="utf-8")
    (project_commands / "dup.md").write_text("Configured command.", encoding="utf-8")
    _write_json(
        tmp_path / "custom.json",
        {
            "commandDirectories": ["project-commands"],
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["custom.json"],
        include_defaults=False,
    )

    assert result.config.command_directories == [project_commands.resolve()]
    assert result.command_registry is not None
    assert result.command_registry.get("dup").content == "Configured command."


def test_loader_consumes_commands_alias(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "commands": {
                "fmt": {
                    "content": "Format $ARGUMENTS",
                    "description": "Format files",
                }
            },
        },
    )

    result = load_runtime_config(tmp_path)

    assert [definition.name for definition in result.command_definitions] == ["fmt"]
    assert result.command_definitions[0].content == "Format $ARGUMENTS"
    assert result.command_definitions[0].description == "Format files"
    assert result.command_registry is not None
    assert result.command_registry.get("fmt").content == "Format $ARGUMENTS"
    assert "commands" not in result.metadata["unconsumed_config"]


def test_agents_mapping_config_generates_agent_registry(tmp_path: Path):
    _write_json(
        tmp_path / "opencode.json",
        {
            "defaultAgent": "general",
            "agents": {
                "general": {
                    "prompt": "General agent.",
                    "tools": {"read_file": True, "write_file": False},
                    "maxIterations": 3,
                    "skills": ["base", "base"],
                    "metadata": {"tier": "default"},
                },
                "review": {
                    "prompt": "Review diffs.",
                    "active_skills": ["review-pr"],
                },
            },
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["opencode.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.default_agent == "general"
    assert registry.names() == ["general", "review"]
    general = registry.resolve(None)
    assert general.name == "general"
    assert general.prompt == "General agent."
    assert general.tools == {"read_file": True, "write_file": False}
    assert general.max_iterations == 3
    assert general.active_skills == ["base"]
    assert general.metadata == {"tier": "default", "mode": "all"}
    assert registry.resolve("review").active_skills == ["review-pr"]


def test_agents_list_config_generates_agent_registry(tmp_path: Path):
    _write_json(
        tmp_path / "agents.json",
        {
            "default_agent": "review",
            "agents": [
                {"name": "review", "prompt": "Review changes."},
                {"name": "debug", "max_iterations": 2, "skills": ["logs"]},
            ],
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=[tmp_path / "agents.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.default_agent == "review"
    assert registry.names() == ["debug", "review"]
    assert registry.resolve("missing").name == "review"
    assert registry.resolve("debug").max_iterations == 2
    assert registry.resolve("debug").active_skills == ["logs"]


def test_agent_singular_alias_is_compatible_with_agents(tmp_path: Path):
    _write_json(
        tmp_path / "agents.json",
        {
            "agent": {
                "review": {
                    "prompt": "Review changes.",
                    "tools": ["read_file", "grep"],
                    "mode": "subagent",
                }
            },
            "agents": [
                {"name": "debug", "prompt": "Debug failures.", "steps": 5},
            ],
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["agents.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.names() == ["debug", "review"]
    assert registry.resolve("review").tools == {"read_file": True, "grep": True}
    assert registry.resolve("review").metadata["mode"] == "subagent"
    assert registry.resolve("debug").max_iterations == 5


def test_markdown_agents_are_loaded_and_config_overrides_same_name(tmp_path: Path):
    _write_text(
        tmp_path / ".opencode" / "agents" / "review.md",
        """
        ---
        description: Markdown review
        tools:
          write_file: false
        model: markdown-model
        ---
        Markdown prompt.
        """,
    )
    _write_json(
        tmp_path / "opencode.json",
        {
            "agentDirectories": [".opencode/agents"],
            "agents": {
                "review": {
                    "description": "Config review",
                    "prompt": "Config prompt.",
                    "tools": {"read_file": True},
                    "model": "config-model",
                },
            },
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["opencode.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.names() == ["review"]
    profile = registry.resolve("review")
    assert profile.description == "Config review"
    assert profile.prompt == "Config prompt."
    assert profile.tools == {"read_file": True}
    assert profile.metadata["model"] == "config-model"


def test_config_agent_overrides_default_markdown_agent(tmp_path: Path):
    _write_text(
        tmp_path / ".opencode" / "agent" / "review.md",
        "Markdown prompt.",
    )
    _write_json(
        tmp_path / "opencode.json",
        {
            "agents": {
                "review": {
                    "description": "Config review",
                    "prompt": "Config prompt.",
                },
            },
        },
    )

    result = load_runtime_config(tmp_path)
    registry = result.agent_registry

    assert registry is not None
    profile = registry.resolve("review")
    assert profile.description == "Config review"
    assert profile.prompt == "Config prompt."


def test_agent_directories_are_resolved_and_used(tmp_path: Path):
    _write_text(
        tmp_path / "profiles" / "debug.md",
        """
        ---
        maxIterations: 7
        ---
        Debug from configured directory.
        """,
    )
    _write_json(
        tmp_path / "opencode.json",
        {"agentDirectories": ["profiles"]},
    )

    result = load_runtime_config(
        tmp_path,
        paths=["opencode.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.names() == ["debug"]
    assert registry.resolve("debug").prompt == "Debug from configured directory."
    assert registry.resolve("debug").max_iterations == 7


def test_max_step_aliases_map_to_agent_max_iterations(tmp_path: Path):
    _write_json(
        tmp_path / "agents.json",
        {
            "agents": [
                {"name": "steps-agent", "steps": 2},
                {"name": "max-steps-agent", "maxSteps": 3},
                {"name": "max-iterations-agent", "maxIterations": 4},
            ],
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["agents.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.resolve("steps-agent").max_iterations == 2
    assert registry.resolve("max-steps-agent").max_iterations == 3
    assert registry.resolve("max-iterations-agent").max_iterations == 4


def test_agent_unknown_fields_are_preserved_in_profile_raw_config(tmp_path: Path):
    _write_json(
        tmp_path / "agents.json",
        {
            "agents": {
                "review": {
                    "prompt": "Review.",
                    "customFlag": "enabled",
                    "future": {"nested": True},
                }
            }
        },
    )

    result = load_runtime_config(
        tmp_path,
        paths=["agents.json"],
        include_defaults=False,
    )
    registry = result.agent_registry

    assert registry is not None
    assert registry.resolve("review").metadata["raw_config"] == {
        "customFlag": "enabled",
        "future": {"nested": True},
    }


def test_agent_loader_keys_are_not_unconsumed_config(tmp_path: Path):
    raw = {
        "agentDirectories": ["missing-agents"],
        "agent": {},
        "agents": [],
        "model": "example-model",
    }
    _write_json(tmp_path / "opencode.json", raw)

    result = load_runtime_config(
        tmp_path,
        paths=["opencode.json"],
        include_defaults=False,
    )

    assert result.agent_registry is None
    assert result.metadata["unconsumed_config"] == {"model": "example-model"}


def test_unconsumed_config_is_preserved_in_metadata(tmp_path: Path):
    raw = {
        "model": "example-model",
        "experimental": {"future": {"enabled": True}},
        "commands": {"fmt": "python -m compileall"},
        "plugins": ["local-plugin"],
        "runtime": {"mode": "build", "future": True},
    }
    _write_json(tmp_path / "opencode.json", raw)

    result = load_runtime_config(tmp_path)

    assert result.raw == raw
    assert result.metadata["raw_config"] == raw
    assert result.metadata["unconsumed_config"] == {
        "model": "example-model",
        "experimental": {"future": {"enabled": True}},
        "plugins": ["local-plugin"],
        "runtime": {"future": True},
    }
    assert result.config.metadata["unconsumed_config"] == result.metadata["unconsumed_config"]


def test_invalid_json_error_includes_file_path(tmp_path: Path):
    path = tmp_path / "opencode.json"
    path.write_text('{"permissions": ', encoding="utf-8")

    with pytest.raises(ValueError) as error:
        load_runtime_config(tmp_path)

    assert str(path) in str(error.value)


def test_config_loader_import_boundary():
    code = """
import json
import sys

import efp_runtime.config_loader

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


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = content.strip("\n").splitlines()
    text = "\n".join(line[8:] if line.startswith("        ") else line for line in lines)
    path.write_text(text, encoding="utf-8")


def _write_skill(root: Path, name: str, *, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir
