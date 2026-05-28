from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ALLOW, ASK, PermissionDecision, PermissionMetadata
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
async def test_glob_matches_sorted_workspace_relative_paths(tmp_path: Path):
    (tmp_path / "pkg" / "nested").mkdir(parents=True)
    (tmp_path / "pkg" / "b.py").write_text("print('b')\n", encoding="utf-8")
    (tmp_path / "pkg" / "nested" / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "pkg" / "nested" / "notes.txt").write_text("skip\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-glob",
            tool_id="glob",
            args={"pattern": "**/*.py", "path": "pkg"},
        )
    )

    assert result.status == "success"
    assert result.output["paths"] == ["pkg/b.py", "pkg/nested/a.py"]
    assert result.output["matches"] == result.output["paths"]
    assert result.output["truncated"] is False

    limited = await runtime.execute(
        ToolCall(
            id="call-glob-limited",
            tool_id="glob",
            args={"pattern": "**/*.py", "path": "pkg", "max_matches": 1},
        )
    )

    assert limited.status == "success"
    assert limited.output["paths"] == ["pkg/b.py"]
    assert limited.output["truncated"] is True


@pytest.mark.asyncio
async def test_glob_rejects_outside_path(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-glob", tool_id="glob", args={"pattern": "*", "path": "../"})
    )

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_edit_replaces_single_match_with_allow_permission(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())

    result = await runtime.execute(
        ToolCall(
            id="call-edit",
            tool_id="edit",
            args={"path": "notes.txt", "old_text": "beta", "new_text": "gamma"},
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "notes.txt"
    assert result.output["replacement_count"] == 1
    assert result.output["bytes"] == len("alpha\ngamma\n".encode("utf-8"))
    assert result.output["changed"] is True
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


@pytest.mark.asyncio
async def test_edit_protects_multiple_matches_unless_replace_all(tmp_path: Path):
    target = tmp_path / "dup.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())

    blocked = await runtime.execute(
        ToolCall(
            id="call-edit-blocked",
            tool_id="edit",
            args={"path": "dup.txt", "old_text": "same", "new_text": "diff"},
        )
    )

    assert blocked.status == "error"
    assert "multiple times" in blocked.error
    assert target.read_text(encoding="utf-8") == "same\nsame\n"

    replaced = await runtime.execute(
        ToolCall(
            id="call-edit-all",
            tool_id="edit",
            args={
                "path": "dup.txt",
                "old_text": "same",
                "new_text": "diff",
                "replace_all": True,
            },
        )
    )

    assert replaced.status == "success"
    assert replaced.output["replacement_count"] == 2
    assert target.read_text(encoding="utf-8") == "diff\ndiff\n"


@pytest.mark.asyncio
async def test_edit_errors_when_old_text_is_missing(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())

    result = await runtime.execute(
        ToolCall(
            id="call-edit-missing",
            tool_id="edit",
            args={"path": "notes.txt", "old_text": "missing", "new_text": "beta"},
        )
    )

    assert result.status == "error"
    assert "old_text was not found" in result.error
    assert target.read_text(encoding="utf-8") == "alpha\n"


@pytest.mark.asyncio
async def test_edit_rejects_outside_path_with_allow_permission(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())

    result = await runtime.execute(
        ToolCall(
            id="call-edit-outside",
            tool_id="edit",
            args={"path": "../outside.txt", "old_text": "a", "new_text": "b"},
        )
    )

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_apply_patch_applies_unified_diff_with_allow_permission(tmp_path: Path):
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())
    patch = textwrap.dedent(
        """\
        diff --git a/hello.txt b/hello.txt
        --- a/hello.txt
        +++ b/hello.txt
        @@ -1 +1 @@
        -hello
        +patched
        """
    )

    result = await runtime.execute(
        ToolCall(id="call-patch", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "success"
    assert result.output["ok"] is True
    assert result.output["paths"] == ["hello.txt"]
    assert target.read_text(encoding="utf-8") == "patched\n"


@pytest.mark.asyncio
async def test_apply_patch_returns_structured_error_on_failed_patch(tmp_path: Path):
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())
    patch = textwrap.dedent(
        """\
        diff --git a/hello.txt b/hello.txt
        --- a/hello.txt
        +++ b/hello.txt
        @@ -1 +1 @@
        -missing
        +patched
        """
    )

    result = await runtime.execute(
        ToolCall(id="call-patch-fail", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "error"
    assert result.success is False
    assert result.output["ok"] is False
    assert result.output["error"] == "Patch check failed."
    assert result.output["paths"] == ["hello.txt"]
    assert target.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_apply_patch_rejects_outside_paths(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path), permission_evaluator=AllowEvaluator())
    patch = textwrap.dedent(
        """\
        --- /dev/null
        +++ b/../outside.txt
        @@ -0,0 +1 @@
        +outside
        """
    )

    result = await runtime.execute(
        ToolCall(id="call-patch-outside", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "error"
    assert result.output["ok"] is False
    assert "Path escapes workspace root." in result.output["error"]


@pytest.mark.asyncio
async def test_todo_write_normalizes_and_validates_status(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-todo",
            tool_id="todo_write",
            args={
                "todos": [
                    {"content": "Inspect tools", "status": "completed"},
                    {"content": "Run tests", "status": "in_progress"},
                ]
            },
        ),
        context=ToolContext(session_id="session-1"),
    )

    assert result.status == "success"
    assert result.output == {
        "todos": [
            {"content": "Inspect tools", "status": "completed"},
            {"content": "Run tests", "status": "in_progress"},
        ]
    }

    invalid = await runtime.execute(
        ToolCall(
            id="call-todo-invalid",
            tool_id="todo_write",
            args={"todos": [{"content": "Bad status", "status": "blocked"}]},
        )
    )

    assert invalid.status == "validation_error"
    assert "todos[0].status" in invalid.error


@pytest.mark.asyncio
async def test_new_core_tool_permission_defaults(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)
    assert registry.ids() == [
        "apply_patch",
        "edit",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "list_dir",
        "read_file",
        "shell_exec",
        "todo_write",
        "write_file",
    ]
    assert registry.require("glob").permission.action == ALLOW
    assert registry.require("fetch").permission.action == ALLOW
    assert registry.require("fetch").permission.category == "network"
    assert registry.require("fetch").permission.risk == "medium"
    assert registry.require("invalid").permission.action == ALLOW
    assert registry.require("invalid").permission.category == "validation"
    assert registry.require("todo_write").permission.action == ALLOW
    assert registry.require("edit").permission.action == ASK
    assert registry.require("apply_patch").permission.action == ASK

    target = tmp_path / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")
    runtime = ToolRuntime(registry)
    result = await runtime.execute(
        ToolCall(
            id="call-edit-permission",
            tool_id="edit",
            args={"path": "notes.txt", "old_text": "alpha", "new_text": "beta"},
        )
    )

    assert result.status == "permission_requested"
    assert target.read_text(encoding="utf-8") == "alpha\n"
