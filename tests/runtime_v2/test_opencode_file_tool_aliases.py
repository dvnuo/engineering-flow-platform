from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionDecision, PermissionMetadata
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


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
async def test_core_registry_includes_file_aliases_and_legacy_ids(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)

    assert {"read", "write", "read_file", "list_dir", "write_file"}.issubset(
        set(registry.ids())
    )
    assert registry.require("read").input_schema == {
        "type": "object",
        "required": ["filePath"],
        "properties": {
            "filePath": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 0},
            "encoding": {"type": "string"},
        },
        "additionalProperties": False,
    }
    assert registry.require("write").input_schema == {
        "type": "object",
        "required": ["filePath", "content"],
        "properties": {
            "filePath": {"type": "string"},
            "content": {"type": "string"},
            "encoding": {"type": "string"},
            "max_diff_lines": {"type": "integer", "minimum": 0},
            "max_diff_chars": {"type": "integer", "minimum": 0},
        },
        "additionalProperties": False,
    }
    assert registry.require("write").permission.action == ASK
    assert registry.require("write_file").permission.action == ASK


@pytest.mark.asyncio
async def test_read_alias_reads_whole_file_using_file_path(tmp_path: Path):
    target = tmp_path / "src" / "app.txt"
    target.parent.mkdir()
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "src/app.txt"})
    )

    assert result.status == "success"
    assert result.output["path"] == "src/app.txt"
    assert result.output["filePath"] == "src/app.txt"
    assert result.output["type"] == "file"
    assert result.output["content"] == "alpha\nbeta\n"
    assert result.output["encoding"] == "utf-8"
    assert result.output["bytes"] == len("alpha\nbeta\n".encode("utf-8"))
    assert result.output["start_line"] == 1
    assert result.output["end_line"] == 2
    assert result.output["total_lines"] == 2
    assert result.output["line_count"] == 2
    assert result.output["has_more"] is False
    assert result.output["next_offset"] is None
    assert result.output["range_truncated"] is False
    assert result.content == (
        "<path>src/app.txt</path>\n"
        "<type>file</type>\n"
        "<content>\n"
        "alpha\nbeta\n"
        "</content>"
    )


@pytest.mark.asyncio
async def test_read_alias_reads_line_range_with_next_offset(tmp_path: Path):
    (tmp_path / "log.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-range",
            tool_id="read",
            args={"filePath": "log.txt", "offset": 2, "limit": 2},
        )
    )

    assert result.status == "success"
    assert result.output["content"] == "two\nthree\n"
    assert result.output["start_line"] == 2
    assert result.output["end_line"] == 3
    assert result.output["total_lines"] == 4
    assert result.output["line_count"] == 2
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 4
    assert result.output["range_truncated"] is True
    assert "<content>\ntwo\nthree\n</content>" in result.content


@pytest.mark.asyncio
async def test_read_alias_reads_directory_entries_with_range_metadata(tmp_path: Path):
    root = tmp_path / "pkg"
    (root / "Alpha").mkdir(parents=True)
    (root / "alpha.txt").write_text("alpha\n", encoding="utf-8")
    (root / "beta.txt").write_text("beta\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-dir",
            tool_id="read",
            args={"filePath": "pkg", "offset": 1, "limit": 2},
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "pkg"
    assert result.output["filePath"] == "pkg"
    assert result.output["type"] == "directory"
    assert [entry["name"] for entry in result.output["entries"]] == [
        "Alpha",
        "alpha.txt",
    ]
    assert result.output["total_entries"] == 3
    assert result.output["offset"] == 1
    assert result.output["limit"] == 2
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 3
    assert result.output["truncated"] is True
    assert result.content == (
        "<path>pkg</path>\n"
        "<type>directory</type>\n"
        "<entries>\n"
        "Alpha/\n"
        "alpha.txt\n"
        "</entries>"
    )


@pytest.mark.asyncio
async def test_read_alias_rejects_path_traversal(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read-outside", tool_id="read", args={"filePath": "../x"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_write_alias_creates_parents_and_returns_diff_metadata(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write",
            args={"filePath": "notes/result.txt", "content": "approved\n"},
        )
    )

    assert result.status == "success"
    assert (tmp_path / "notes" / "result.txt").read_text(encoding="utf-8") == (
        "approved\n"
    )
    assert result.output["path"] == "notes/result.txt"
    assert result.output["filePath"] == "notes/result.txt"
    assert result.output["bytes"] == len("approved\n".encode("utf-8"))
    assert result.output["old_bytes"] == 0
    assert result.output["new_bytes"] == len("approved\n".encode("utf-8"))
    assert result.output["changed"] is True
    assert result.output["created"] is True
    assert result.output["diff_truncated"] is False
    assert "--- a/notes/result.txt" in result.output["diff"]
    assert "+++ b/notes/result.txt" in result.output["diff"]
    assert "+approved" in result.output["diff"]
    assert result.content.startswith(
        "Wrote notes/result.txt: created, bytes=9, old_bytes=0, new_bytes=9."
    )
    assert "```diff" in result.content


@pytest.mark.asyncio
async def test_write_alias_rejects_path_traversal(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-write-outside",
            tool_id="write",
            args={"filePath": "../outside.txt", "content": "nope"},
        )
    )

    assert result.status == "error"
    assert result.success is False
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_agent_runtime_provider_request_schemas_include_aliases(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("List available tools.", session_id="session-aliases")

    assert result.status == LoopStatus.COMPLETED
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]
    assert "read" in schema_ids
    assert "write" in schema_ids
    assert "read_file" in schema_ids
    assert "list_dir" in schema_ids
    assert "write_file" in schema_ids
