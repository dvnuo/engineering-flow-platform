from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.instructions import ReadInstructionResolver
from efp_runtime.models import ToolCall
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_default_read_file_output_shape_stays_compatible(tmp_path: Path):
    (tmp_path / "app.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "app.txt"})
    )

    assert result.status == "success"
    assert result.output["path"] == "app.txt"
    assert result.output["filePath"] == "app.txt"
    assert result.output["content"] == "alpha\nbeta\n"
    assert result.output["encoding"] == "utf-8"
    assert result.output["bytes"] == len("alpha\nbeta\n".encode("utf-8"))


@pytest.mark.asyncio
async def test_read_file_offset_and_limit_return_requested_lines(tmp_path: Path):
    content = "one\ntwo\nthree\nfour\n"
    (tmp_path / "log.txt").write_text(content, encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read-range",
            tool_id="read",
            args={"filePath": "log.txt", "offset": 2, "limit": 2},
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "log.txt"
    assert result.output["filePath"] == "log.txt"
    assert result.output["content"] == "two\nthree\n"
    assert result.output["encoding"] == "utf-8"
    assert result.output["bytes"] == len(content.encode("utf-8"))
    assert result.output["start_line"] == 2
    assert result.output["end_line"] == 3
    assert result.output["total_lines"] == 4
    assert result.output["line_count"] == 2
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 4
    assert result.output["range_truncated"] is True
    assert result.output["returned_bytes"] == len("two\nthree\n".encode("utf-8"))


@pytest.mark.asyncio
async def test_read_file_limit_zero_returns_empty_range_metadata(tmp_path: Path):
    content = "one\ntwo\n"
    (tmp_path / "log.txt").write_text(content, encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read-empty-range",
            tool_id="read",
            args={"filePath": "log.txt", "offset": 2, "limit": 0},
        )
    )

    assert result.status == "success"
    assert result.output["content"] == ""
    assert result.output["bytes"] == len(content.encode("utf-8"))
    assert result.output["returned_bytes"] == 0
    assert result.output["start_line"] == 2
    assert result.output["end_line"] == 1
    assert result.output["total_lines"] == 2
    assert result.output["line_count"] == 0
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 2
    assert result.output["range_truncated"] is True


@pytest.mark.asyncio
async def test_read_file_offset_past_eof_returns_empty_range(tmp_path: Path):
    (tmp_path / "log.txt").write_text("one\ntwo\n", encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read-past-eof",
            tool_id="read",
            args={"filePath": "log.txt", "offset": 5},
        )
    )

    assert result.status == "error"
    assert "Offset 5 is out of range" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("args", "field"),
    [
        ({"offset": 0}, "offset"),
        ({"limit": -1}, "limit"),
        ({"offset": "2"}, "offset"),
        ({"limit": "2"}, "limit"),
    ],
)
async def test_read_file_rejects_invalid_range_arguments(
    tmp_path: Path,
    args: dict[str, Any],
    field: str,
):
    (tmp_path / "log.txt").write_text("one\n", encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id=f"call-read-invalid-{field}",
            tool_id="read",
            args={"filePath": "log.txt", **args},
        )
    )

    assert result.status == "validation_error"
    assert field in result.error


@pytest.mark.asyncio
async def test_read_file_range_still_attaches_nearby_instructions(tmp_path: Path):
    package_dir = tmp_path / "src" / "pkg"
    package_dir.mkdir(parents=True)
    (package_dir / "app.py").write_text("line1\nline2\n", encoding="utf-8")
    (package_dir / "AGENTS.md").write_text("Package instructions.", encoding="utf-8")
    runtime = _runtime_with_resolver(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read-range-instructions",
            tool_id="read",
            args={"filePath": "src/pkg/app.py", "offset": 2, "limit": 1},
        )
    )

    assert result.status == "success"
    assert result.output["content"] == "line2\n"
    assert result.output["total_lines"] == 2
    assert result.output["line_count"] == 1
    assert result.output["loaded_instruction_paths"] == ["src/pkg/AGENTS.md"]
    assert result.output["instructions"][0]["content"] == "Package instructions."


@pytest.mark.asyncio
async def test_read_file_range_reads_archived_tool_output_path(tmp_path: Path):
    output_dir = tmp_path / ".efp_runtime" / "tool-output"
    output_dir.mkdir(parents=True)
    (output_dir / "call-shell.log").write_text("out1\nout2\nout3\n", encoding="utf-8")
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-read-output-path",
            tool_id="read",
            args={
                "filePath": ".efp_runtime/tool-output/call-shell.log",
                "offset": 2,
                "limit": 2,
            },
        )
    )

    assert result.status == "success"
    assert result.output["path"] == ".efp_runtime/tool-output/call-shell.log"
    assert result.output["content"] == "out2\nout3\n"
    assert result.output["start_line"] == 2
    assert result.output["end_line"] == 3
    assert result.output["total_lines"] == 3
    assert result.output["has_more"] is False
    assert result.output["next_offset"] is None
    assert result.output["range_truncated"] is False


def test_read_file_range_import_boundary():
    code = """
import json
import sys
from pathlib import Path

from efp_runtime.tools.builtin.filesystem import create_read_tool

create_read_tool(Path(".").resolve())
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

    source = (ROOT / "src/efp_runtime/tools/builtin/filesystem.py").read_text(
        encoding="utf-8"
    )
    assert "from src.efp_runtime" not in source
    assert "import src.efp_runtime" not in source


def _runtime(workspace_root: Path) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(workspace_root)
    )


def _runtime_with_resolver(workspace_root: Path) -> ToolRuntime:
    resolver = ReadInstructionResolver(workspace_root)
    return ToolRuntime(
        create_core_tool_registry(
            workspace_root,
            instruction_resolver=resolver,
            
        )
    )
