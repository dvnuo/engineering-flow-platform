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
    assert result.output["default_limit_applied"] is True
    assert result.output["max_visible_bytes"] == 50 * 1024
    assert result.output["max_line_length"] == 2000
    assert result.output["truncated_by"] == []
    assert result.content == (
        "<path>src/app.txt</path>\n"
        "<type>file</type>\n"
        "<content>\n"
        "1: alpha\n"
        "2: beta\n"
        "\n"
        "(End of file - total 2 lines)\n"
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
    assert result.output["default_limit_applied"] is False
    assert result.output["truncated_by"] == ["lines"]
    assert (
        "<content>\n"
        "2: two\n"
        "3: three\n"
        "\n"
        "(Showing lines 2-3 of 4. Use offset=4 to continue.)\n"
        "</content>"
    ) in result.content


@pytest.mark.asyncio
async def test_read_alias_defaults_to_2000_visible_lines(tmp_path: Path):
    lines = [f"line {index}\n" for index in range(1, 2006)]
    (tmp_path / "large.txt").write_text("".join(lines), encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read-default-limit", tool_id="read", args={"filePath": "large.txt"})
    )

    assert result.status == "success"
    assert result.output["content"] == "".join(lines[:2000])
    assert result.output["start_line"] == 1
    assert result.output["end_line"] == 2000
    assert result.output["total_lines"] == 2005
    assert result.output["line_count"] == 2000
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 2001
    assert result.output["range_truncated"] is True
    assert result.output["default_limit_applied"] is True
    assert result.output["truncated_by"] == ["lines"]
    assert "1: line 1" in result.content
    assert "2000: line 2000" in result.content
    assert "(Showing lines 1-2000 of 2005. Use offset=2001 to continue.)" in result.content


@pytest.mark.asyncio
async def test_read_alias_limit_zero_returns_empty_range_metadata(tmp_path: Path):
    (tmp_path / "log.txt").write_text("one\ntwo\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-zero",
            tool_id="read",
            args={"filePath": "log.txt", "limit": 0},
        )
    )

    assert result.status == "success"
    assert result.output["content"] == ""
    assert result.output["line_count"] == 0
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 1
    assert result.output["range_truncated"] is True
    assert result.output["default_limit_applied"] is False
    assert result.output["truncated_by"] == ["lines"]
    assert "(Showing lines 1-0 of 2. Use offset=1 to continue.)" in result.content


@pytest.mark.asyncio
async def test_read_alias_rejects_offset_beyond_eof(tmp_path: Path):
    (tmp_path / "log.txt").write_text("one\ntwo\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-past-eof",
            tool_id="read",
            args={"filePath": "log.txt", "offset": 3},
        )
    )

    assert result.status == "error"
    assert result.error == "Offset 3 is out of range for this file (2 lines)."


@pytest.mark.asyncio
async def test_read_alias_allows_empty_file_at_first_offset(tmp_path: Path):
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-empty",
            tool_id="read",
            args={"filePath": "empty.txt", "offset": 1},
        )
    )

    assert result.status == "success"
    assert result.output["content"] == ""
    assert result.output["total_lines"] == 0
    assert result.output["line_count"] == 0
    assert result.content == (
        "<path>empty.txt</path>\n"
        "<type>file</type>\n"
        "<content>\n"
        "\n"
        "(End of file - total 0 lines)\n"
        "</content>"
    )


@pytest.mark.asyncio
async def test_read_alias_caps_bytes_and_long_lines(tmp_path: Path):
    suffix = "... (line truncated to 2000 chars)"
    (tmp_path / "long.txt").write_text(("x" * 2500 + "\n") * 40, encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read-byte-cap", tool_id="read", args={"filePath": "long.txt"})
    )

    assert result.status == "success"
    assert result.output["returned_bytes"] <= 50 * 1024
    assert result.output["line_count"] < result.output["total_lines"]
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == result.output["line_count"] + 1
    assert result.output["range_truncated"] is True
    assert "line_length" in result.output["truncated_by"]
    assert "bytes" in result.output["truncated_by"]
    assert suffix in result.output["content"]
    assert "x" * 2100 not in result.output["content"]
    assert "(Output capped at 50 KB." in result.content


@pytest.mark.asyncio
async def test_read_alias_rejects_binary_content(tmp_path: Path):
    (tmp_path / "blob.bin").write_bytes(b"text\x00binary")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read-binary", tool_id="read", args={"filePath": "blob.bin"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "File is binary and cannot be read as text: blob.bin" in result.error


@pytest.mark.asyncio
async def test_read_alias_missing_file_includes_same_directory_suggestions(
    tmp_path: Path,
):
    src = tmp_path / "src"
    src.mkdir()
    for name in ["Alpha1.py", "Alpha2.py", "Alpha3.py", "Alpha4.py"]:
        (src / name).write_text("pass\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-missing",
            tool_id="read",
            args={"filePath": "src/alpha"},
        )
    )

    assert result.status == "error"
    assert "Path does not exist: src/alpha" in result.error
    assert "Did you mean one of these?" in result.error
    assert "src/Alpha1.py" in result.error
    assert "src/Alpha2.py" in result.error
    assert "src/Alpha3.py" in result.error
    assert "src/Alpha4.py" not in result.error


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
    assert result.output["default_limit_applied"] is False
    assert result.content == (
        "<path>pkg</path>\n"
        "<type>directory</type>\n"
        "<entries>\n"
        "Alpha/\n"
        "alpha.txt\n"
        "\n"
        "(Showing 2 of 3 entries. Use offset=3 to read beyond entry 2)\n"
        "</entries>"
    )


@pytest.mark.asyncio
async def test_read_alias_directory_defaults_to_2000_entries(tmp_path: Path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(2005):
        (root / f"entry-{index:04}.txt").write_text("", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-read-dir-default-limit",
            tool_id="read",
            args={"filePath": "many"},
        )
    )

    assert result.status == "success"
    assert len(result.output["entries"]) == 2000
    assert result.output["total_entries"] == 2005
    assert result.output["offset"] == 1
    assert result.output["limit"] == 2000
    assert result.output["has_more"] is True
    assert result.output["next_offset"] == 2001
    assert result.output["truncated"] is True
    assert result.output["default_limit_applied"] is True
    assert "entry-0000.txt" in result.content
    assert "entry-1999.txt" in result.content
    assert "entry-2000.txt" not in result.content
    assert (
        "(Showing 2000 of 2005 entries. Use offset=2001 to read beyond entry 2000)"
        in result.content
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
