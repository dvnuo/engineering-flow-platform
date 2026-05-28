from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


@pytest.mark.asyncio
async def test_read_file_and_list_dir_inside_workspace(tmp_path: Path):
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "app.py").write_text("print('hello')\n", encoding="utf-8")

    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    read_result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read_file", args={"path": "src/app.py"})
    )
    list_result = await runtime.execute(
        ToolCall(id="call-list", tool_id="list_dir", args={"path": "src"})
    )

    assert read_result.status == "success"
    assert read_result.output == {
        "path": "src/app.py",
        "content": "print('hello')\n",
        "encoding": "utf-8",
        "bytes": 15,
    }
    assert list_result.status == "success"
    assert list_result.output == {
        "path": "src",
        "entries": [
            {
                "name": "app.py",
                "path": "src/app.py",
                "type": "file",
                "size": 15,
            }
        ],
    }


@pytest.mark.asyncio
async def test_path_traversal_is_rejected(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read_file", args={"path": "../outside.txt"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_write_requires_permission_by_default_and_does_not_write(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))
    target = tmp_path / "created.txt"

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write_file",
            args={"path": "created.txt", "content": "blocked"},
        )
    )

    assert result.status == "permission_requested"
    assert result.success is False
    assert target.exists() is False


@pytest.mark.asyncio
async def test_write_succeeds_with_allow_evaluator(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write_file",
            args={
                "path": "notes/result.txt",
                "content": "approved\n",
                "create_dirs": True,
            },
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "notes/result.txt"
    assert result.output["bytes"] == 9
    assert (tmp_path / "notes/result.txt").read_text(encoding="utf-8") == "approved\n"


@pytest.mark.asyncio
async def test_grep_finds_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("no match\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-grep", tool_id="grep", args={"pattern": "needle", "path": "."})
    )

    assert result.status == "success"
    assert result.output == {
        "pattern": "needle",
        "path": ".",
        "matches": [
            {
                "path": "a.txt",
                "line_number": 2,
                "column": 1,
                "line": "needle here",
            }
        ],
        "files_searched": 2,
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_shell_requires_permission_by_default(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-shell", tool_id="shell_exec", args={"command": "printf ok"})
    )

    assert result.status == "permission_requested"
    assert result.success is False


@pytest.mark.asyncio
async def test_shell_succeeds_with_allow_evaluator(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell",
            tool_id="shell_exec",
            args={"command": "printf 'ok\\n'", "timeout": 5},
        )
    )

    assert result.status == "success"
    assert result.output == {
        "stdout": "ok\n",
        "stderr": "",
        "exit_code": 0,
        "timed_out": False,
        "cwd": ".",
    }


def test_builtin_tools_import_standalone_without_legacy_modules():
    code = """
import json
import sys
from pathlib import Path

from efp_runtime.tools.builtin import create_core_tool_registry

registry = create_core_tool_registry(Path(".").resolve())
legacy_modules = [
    "src.agents.core",
    "src.bash_tools",
    "src.github",
    "src.jira",
    "src.confluence",
    "src.git",
    "src.context_tools",
]
print(json.dumps({
    "ids": registry.ids(),
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
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

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ids": ["grep", "list_dir", "read_file", "shell_exec", "write_file"],
        "legacy_loaded": [],
    }


def test_builtin_tool_source_stays_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/tools/builtin").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "src.agents.core",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
        "src.agents.tool_result_policy",
        "src.bash_tools",
        "src.github",
        "src.jira",
        "src.confluence",
        "src.git",
        "src.context_tools",
    ]

    for token in forbidden_tokens:
        assert token not in combined
