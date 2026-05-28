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
    assert config.runtime_mode == "plan"


def test_default_skill_directories_precede_configured_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    home = tmp_path / "home"
    global_claude = home / ".claude" / "skills"
    global_agents = home / ".agents" / "skills"
    opencode_skill = tmp_path / ".opencode" / "skill"
    opencode_skills = tmp_path / ".opencode" / "skills"
    claude_skills = tmp_path / ".claude" / "skills"
    agents_skills = tmp_path / ".agents" / "skills"
    for directory in (
        global_claude,
        global_agents,
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
        opencode_skill.resolve(),
        opencode_skills.resolve(),
        claude_skills.resolve(),
        agents_skills.resolve(),
        (tmp_path / "skills").resolve(),
    ]


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
    monkeypatch.setenv("HOME", str(home))
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
):
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
