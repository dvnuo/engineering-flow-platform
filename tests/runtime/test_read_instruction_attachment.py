from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.instructions import ReadInstructionResolver
from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_read_file_without_instructions_keeps_existing_output_shape(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "src/app.py"})
    )

    assert result.status == "success"
    assert result.output["path"] == "src/app.py"
    assert result.output["filePath"] == "src/app.py"
    assert result.output["content"] == "print('hello')\n"
    assert result.output["encoding"] == "utf-8"
    assert result.output["bytes"] == 15


@pytest.mark.asyncio
async def test_read_file_attaches_nearby_instructions_from_near_to_far(tmp_path: Path):
    package_dir = tmp_path / "src" / "pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "app.py").write_text("print('app')\n", encoding="utf-8")
    (package_dir / "AGENTS.md").write_text("Package agents.", encoding="utf-8")
    (package_dir / "CLAUDE.md").write_text("Ignored package claude.", encoding="utf-8")
    (tmp_path / "src" / "CLAUDE.md").write_text("Source claude.", encoding="utf-8")
    (tmp_path / "src" / "CONTEXT.md").write_text("Ignored source context.", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Workspace agents.", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Ignored workspace claude.", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "src/pkg/app.py"})
    )

    assert result.status == "success"
    assert result.output["loaded_instruction_paths"] == [
        "src/pkg/AGENTS.md",
        "AGENTS.md",
    ]
    assert [entry["path"] for entry in result.output["instructions"]] == [
        "src/pkg/AGENTS.md",
        "AGENTS.md",
    ]
    assert [entry["content"] for entry in result.output["instructions"]] == [
        "Package agents.",
        "Workspace agents.",
    ]


@pytest.mark.asyncio
async def test_read_file_does_not_attach_instruction_file_to_itself(tmp_path: Path):
    package_dir = tmp_path / "src" / "pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "AGENTS.md").write_text("Package agents.", encoding="utf-8")
    (tmp_path / "src" / "CLAUDE.md").write_text("Source claude.", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Workspace agents.", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read",
            tool_id="read",
            args={"filePath": "src/pkg/AGENTS.md"},
        )
    )

    assert result.status == "success"
    assert result.output["loaded_instruction_paths"] == [
        "AGENTS.md",
    ]


@pytest.mark.asyncio
async def test_read_file_instruction_truncation_metadata(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("abcdef", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path, max_instruction_chars=3)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "app.py"})
    )

    instruction = result.output["instructions"][0]
    assert instruction == {
        "path": "AGENTS.md",
        "content": "abc",
        "truncated": True,
        "original_chars": 6,
    }


@pytest.mark.asyncio
async def test_agent_runtime_default_registry_attaches_read_instructions(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Workspace agents.", encoding="utf-8")
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
        ),
    )

    result = await runtime.tool_runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "app.py"})
    )

    assert result.status == "success"
    assert result.output["loaded_instruction_paths"] == ["AGENTS.md"]


@pytest.mark.asyncio
async def test_read_file_does_not_attach_system_loaded_instruction_again(
    tmp_path: Path,
):
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    instruction = tmp_path / "AGENTS.md"
    instruction.write_text("Workspace agents.", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "app.py"}),
        context=ToolContext(
            metadata={"system_instruction_paths": [str(instruction.resolve())]},
        ),
    )

    assert result.status == "success"
    assert "loaded_instruction_paths" not in result.output
    assert "instructions" not in result.output


@pytest.mark.asyncio
async def test_agent_runtime_can_disable_read_instruction_attachment(tmp_path: Path):
    (tmp_path / "app.py").write_text("print('app')\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Workspace agents.", encoding="utf-8")
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=2,
            attach_read_instructions=False,
        ),
    )

    result = await runtime.tool_runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "app.py"})
    )

    assert result.status == "success"
    assert result.output["path"] == "app.py"
    assert result.output["filePath"] == "app.py"
    assert result.output["content"] == "print('app')\n"
    assert result.output["encoding"] == "utf-8"
    assert result.output["bytes"] == 13
    assert "loaded_instruction_paths" not in result.output


@pytest.mark.asyncio
async def test_read_file_path_traversal_is_still_rejected_with_resolver(tmp_path: Path):
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "../outside.txt"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "Path escapes workspace root." in result.error


def test_read_instruction_attachment_import_boundary():
    code = """
import json
import sys
from pathlib import Path

from efp_runtime.instructions import ReadInstructionResolver
from efp_runtime.tools.builtin import create_core_tool_registry

ReadInstructionResolver(Path(".").resolve())
create_core_tool_registry(Path(".").resolve())
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


def _runtime_with_resolver(
    workspace_root: Path,
    *,
    max_instruction_chars: int = 20000,
) -> ToolRuntime:
    resolver = ReadInstructionResolver(
        workspace_root,
        max_instruction_chars=max_instruction_chars,
    )
    return ToolRuntime(
        create_core_tool_registry(
            workspace_root,
            instruction_resolver=resolver,
        )
    )
