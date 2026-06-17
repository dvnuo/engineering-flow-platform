from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from efp_runtime.agents.discovery import (
    discover_agent_profiles,
    load_agent_registry,
)


ROOT = Path(__file__).resolve().parents[2]


def test_discovers_markdown_agent_with_frontmatter_and_prompt(tmp_path: Path):
    agent_dir = tmp_path / ".efp" / "agents"
    _write(
        agent_dir / "review.md",
        """
        ---
        name: code-reviewer
        description: Review code changes
        tools: [read_file, grep]
        mode: subagent
        model: gpt-test
        ---
        Inspect the diff and report actionable findings.
        """,
    )

    profiles = discover_agent_profiles([agent_dir])

    assert [profile.name for profile in profiles] == ["code-reviewer"]
    profile = profiles[0]
    assert profile.description == "Review code changes"
    assert profile.prompt == "Inspect the diff and report actionable findings."
    assert profile.tools == {"read_file": True, "grep": True}
    assert profile.metadata["mode"] == "subagent"
    assert profile.metadata["model"] == "gpt-test"


def test_nested_tools_mapping_and_skills_shorthand(tmp_path: Path):
    agent_dir = tmp_path / ".efp" / "agents"
    _write(
        agent_dir / "debug.markdown",
        """
        ---
        description: Debug failures
        tools:
          write_file: false
          shell_exec: true
        permission:
          edit: ask
        skills: [logs, tests, logs]
        maxSteps: 4
        hidden: true
        ---
        Diagnose the failure.
        """,
    )

    profile = discover_agent_profiles([agent_dir])[0]

    assert profile.name == "debug"
    assert profile.tools == {"write_file": False, "shell_exec": True}
    assert profile.active_skills == ["logs", "tests"]
    assert profile.max_iterations == 4
    assert profile.metadata["permission"] == {"edit": "ask"}
    assert profile.metadata["hidden"] is True
    assert profile.metadata["mode"] == "all"


def test_disabled_markdown_agent_is_skipped(tmp_path: Path):
    agent_dir = tmp_path / ".efp" / "agents"
    _write(
        agent_dir / "skip.md",
        """
        ---
        disable: true
        ---
        Do not load me.
        """,
    )

    assert discover_agent_profiles([agent_dir]) == []


def test_duplicate_agent_name_later_discovery_overrides_earlier(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write(first / "review.md", "First prompt.")
    _write(second / "review.md", "Second prompt.")

    registry = load_agent_registry([first, second])

    assert registry.names() == ["review"]
    assert registry.resolve("review").prompt == "Second prompt."


def test_hidden_subdirectories_are_not_scanned(tmp_path: Path):
    agent_dir = tmp_path / ".efp" / "agents"
    _write(agent_dir / "visible.md", "Visible prompt.")
    _write(agent_dir / ".hidden" / "hidden.md", "Hidden prompt.")

    profiles = discover_agent_profiles([agent_dir])

    assert [profile.name for profile in profiles] == ["visible"]


def test_unknown_fields_are_preserved_in_raw_config(tmp_path: Path):
    agent_dir = tmp_path / ".efp" / "agents"
    _write(
        agent_dir / "review.md",
        """
        ---
        customFlag: enabled
        threshold: 2
        temperature: 0.25
        topP: 0.9
        ---
        Review with custom metadata.
        """,
    )

    profile = discover_agent_profiles([agent_dir])[0]

    assert profile.metadata["temperature"] == 0.25
    assert profile.metadata["top_p"] == 0.9
    assert profile.metadata["raw_config"] == {
        "customFlag": "enabled",
        "threshold": 2,
    }


def test_agent_discovery_import_boundary():
    code = """
import json
import sys

import efp_runtime.agents.discovery

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

    assert result.stdout.strip().splitlines()[-1] == "[]"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dedent(content), encoding="utf-8")


def _dedent(content: str) -> str:
    lines = content.strip("\n").splitlines()
    return "\n".join(line[8:] if line.startswith("        ") else line for line in lines)
