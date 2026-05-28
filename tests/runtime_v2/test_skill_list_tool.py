from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.agents.profile import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.skills.tool import build_skill_list_tool
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_skill_list_returns_multiple_skills_in_stable_order(tmp_path: Path):
    _write_skill(tmp_path, "beta", description="Beta skill")
    alpha = _write_skill(tmp_path, "alpha", description="Alpha skill")
    (alpha / "guide.md").write_text("Guide", encoding="utf-8")

    result = await _run_skill_list(tmp_path)

    assert result.status == "success"
    assert result.success is True
    assert result.output["count"] == 2
    assert [skill["name"] for skill in result.output["skills"]] == ["alpha", "beta"]
    assert [skill["description"] for skill in result.output["skills"]] == [
        "Alpha skill",
        "Beta skill",
    ]
    assert [skill["sidecar_count"] for skill in result.output["skills"]] == [1, 0]
    assert result.content.startswith("<available_skills>")
    assert "- alpha: Alpha skill (1 sidecar file)" in result.content
    assert "- beta: Beta skill (0 sidecar files)" in result.content
    assert result.content.rstrip().endswith("</active_skills>")


@pytest.mark.asyncio
async def test_skill_list_preserves_frontmatter_metadata(tmp_path: Path):
    skill_dir = tmp_path / "metadata-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: metadata-skill\n"
        "license: MIT\n"
        "compatibility: runtime-v2\n"
        "category: review\n"
        "version: 1.2.3\n"
        "author: Runtime Team\n"
        "customScalar: preserved\n"
        "---\n"
        "# Metadata Skill\n",
        encoding="utf-8",
    )

    result = await _run_skill_list(tmp_path)

    [skill] = result.output["skills"]
    assert skill["name"] == "metadata-skill"
    assert skill["description"] == ""
    assert skill["metadata"] == {
        "name": "metadata-skill",
        "license": "MIT",
        "compatibility": "runtime-v2",
        "category": "review",
        "version": "1.2.3",
        "author": "Runtime Team",
        "customScalar": "preserved",
    }
    assert "- metadata-skill:" in result.content


@pytest.mark.asyncio
async def test_skill_list_can_omit_sidecar_details(tmp_path: Path):
    skill_dir = _write_skill(tmp_path, "review")
    (skill_dir / "guide.md").write_text("Guide", encoding="utf-8")

    result = await _run_skill_list(tmp_path, args={"include_sidecars": False})

    [skill] = result.output["skills"]
    assert skill["sidecar_count"] == 1
    assert skill["sidecars"] == []


@pytest.mark.asyncio
async def test_skill_list_marks_text_and_binary_sidecars_without_content(tmp_path: Path):
    skill_dir = _write_skill(tmp_path, "review")
    (skill_dir / "guide.md").write_text("Readable guide", encoding="utf-8")
    (skill_dir / "asset.bin").write_bytes(b"\x00\xffbinary")

    result = await _run_skill_list(tmp_path)

    [skill] = result.output["skills"]
    sidecars = {entry["path"]: entry for entry in skill["sidecars"]}
    assert sidecars["guide.md"]["content_type"] == "text"
    assert sidecars["asset.bin"]["content_type"] == "binary"
    assert "content" not in sidecars["guide.md"]
    assert "content" not in sidecars["asset.bin"]


@pytest.mark.asyncio
async def test_agent_runtime_provider_schema_includes_skill_and_skill_list(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "review")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            max_iterations=1,
        ),
    )

    result = await runtime.run("List tools.", session_id="session-skill-list-schema")

    assert result.status == LoopStatus.COMPLETED
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]
    assert "skill" in schema_ids
    assert "skill_list" in schema_ids


@pytest.mark.asyncio
async def test_enable_skill_list_tool_false_hides_skill_list(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "review")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            enable_skill_list_tool=False,
            max_iterations=1,
        ),
    )

    result = await runtime.run("List tools.", session_id="session-no-skill-list")

    assert result.status == LoopStatus.COMPLETED
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]
    assert "skill" in schema_ids
    assert "skill_list" not in schema_ids


@pytest.mark.asyncio
async def test_enable_skill_list_tool_true_exposes_empty_skill_list(tmp_path: Path):
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            enable_skill_list_tool=True,
            max_iterations=1,
        ),
    )

    result = await runtime.run("List tools.", session_id="session-empty-skill-list")

    assert result.status == LoopStatus.COMPLETED
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]
    assert "skill" not in schema_ids
    assert "skill_list" in schema_ids


@pytest.mark.asyncio
async def test_active_skills_are_visible_to_metadata_and_skill_list(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "review-pr")
    _write_skill(skills_dir, "triage")
    provider = ScriptedLLMProvider(
        [
            {"tool_calls": [_tool_call("call-skill-list", "skill_list", {})]},
            {"content": "Done."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            skill_directories=[skills_dir],
            max_iterations=2,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    result = await runtime.run(
        "/skill review-pr\nShow skill state.",
        session_id="session-active-skill-list",
    )

    assert result.status == LoopStatus.COMPLETED
    first_request = provider.requests[0]
    assert first_request.metadata["active_skills"] == ["review-pr"]
    assert first_request.metadata["active_skill_count"] == 1
    assert first_request.metadata["available_skill_count"] == 2
    tool_results = [
        result
        for message in provider.requests[1].provider_request.messages
        for result in message.tool_results
    ]
    assert len(tool_results) == 1
    assert tool_results[0].tool_name == "skill_list"
    assert tool_results[0].output["active_skills"] == ["review-pr"]
    assert tool_results[0].metadata["tool_result_metadata"]["active_skill_count"] == 1


@pytest.mark.asyncio
async def test_skill_clear_updates_active_skill_metadata(tmp_path: Path):
    _write_skill(tmp_path, "review-pr")
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            skill_directories=[tmp_path],
            active_skills=["review-pr"],
            max_iterations=1,
            include_default_system_prompt=False,
            include_runtime_reminders=False,
        ),
    )

    result = await runtime.run(
        "/skill clear\nContinue.",
        session_id="session-clear-skill-list",
    )

    assert result.status == LoopStatus.COMPLETED
    assert runtime.active_skills == []
    request = provider.requests[0]
    assert request.metadata["active_skills"] == []
    assert request.metadata["active_skill_count"] == 0
    assert request.metadata["available_skill_count"] == 1


def test_child_config_preserves_enable_skill_list_tool(tmp_path: Path):
    base_config = RuntimeConfig(
        workspace_root=tmp_path,
        enable_skill_list_tool=False,
        max_iterations=3,
    )

    child = _child_config(
        profile=AgentProfile(name="debugger"),
        base_config=base_config,
        workspace_root=None,
        metadata={"child": True},
    )

    assert child.enable_skill_list_tool is False


def test_skill_list_import_boundary():
    code = """
import json
import sys

import efp_runtime.skills.tool

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


def test_skill_list_source_stays_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
    )

    assert "from src.efp_runtime" not in combined
    assert "import src.efp_runtime" not in combined


async def _run_skill_list(
    root: Path,
    *,
    args: dict | None = None,
):
    runtime = ToolRuntime(
        ToolRegistry([build_skill_list_tool(SkillDiscovery([root]))])
    )
    return await runtime.execute(
        ToolCall(id="call-skill-list", tool_id="skill_list", args=args or {})
    )


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "Loads skill context",
    content: str = "# Skill\nUse this context.",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{content}\n",
        encoding="utf-8",
    )
    return skill_dir


def _tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, sort_keys=True),
        },
    }
