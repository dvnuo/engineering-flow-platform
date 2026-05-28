from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.config_loader import (
    find_runtime_config_files,
    load_runtime_config,
)


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
    assert config.runtime_mode == "plan"


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

    result = load_runtime_config(tmp_path)
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
    assert general.metadata == {"tier": "default"}
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

    result = load_runtime_config(tmp_path, paths=[tmp_path / "agents.json"], include_defaults=False)
    registry = result.agent_registry

    assert registry is not None
    assert registry.default_agent == "review"
    assert registry.names() == ["debug", "review"]
    assert registry.resolve("missing").name == "review"
    assert registry.resolve("debug").max_iterations == 2
    assert registry.resolve("debug").active_skills == ["logs"]


def test_unconsumed_config_is_preserved_in_metadata(tmp_path: Path):
    raw = {
        "model": "example-model",
        "mcp": {"server": {"command": "noop"}},
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
        "mcp": {"server": {"command": "noop"}},
        "commands": {"fmt": "python -m compileall"},
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
