from __future__ import annotations

import json
import os
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
    os.utime(tmp_path / "pkg" / "b.py", (100, 100))
    os.utime(tmp_path / "pkg" / "nested" / "a.py", (200, 200))
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-glob",
            tool_id="glob",
            args={"pattern": "**/*.py", "path": "pkg"},
        )
    )

    assert result.status == "success"
    assert result.output["paths"] == ["pkg/nested/a.py", "pkg/b.py"]
    assert result.output["matches"] == result.output["paths"]
    assert result.output["truncated"] is False
    assert result.content == "pkg/nested/a.py\npkg/b.py"

    limited = await runtime.execute(
        ToolCall(
            id="call-glob-limited",
            tool_id="glob",
            args={"pattern": "**/*.py", "path": "pkg", "max_matches": 1},
        )
    )

    assert limited.status == "success"
    assert limited.output["paths"] == ["pkg/nested/a.py"]
    assert limited.output["truncated"] is True
    assert "Results are truncated" in limited.content


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
    filediff = replaced.output["filediff"]
    assert replaced.metadata["filediff"] == filediff
    assert filediff["path"] == "dup.txt"
    assert filediff["old_path"] == "dup.txt"
    assert filediff["additions"] == 2
    assert filediff["deletions"] == 2
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
    assert result.output["filediffs"] == [result.output["filediff"]]
    assert result.metadata["filediffs"] == result.output["filediffs"]
    assert result.metadata["filediff"] == result.output["filediff"]
    assert result.output["filediff"] == {
        "path": "hello.txt",
        "old_path": "hello.txt",
        "additions": 1,
        "deletions": 1,
        "patch": patch,
    }
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
async def test_todo_write_normalizes_metadata_events_and_validates_input(
    tmp_path: Path,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-todo",
            tool_id="todo_write",
            args={
                "todos": [
                    {"content": "Inspect tools", "status": "completed"},
                    {"content": "Run tests", "status": "in_progress"},
                    {
                        "content": "Drop stale task",
                        "status": "cancelled",
                        "priority": "low",
                    },
                ]
            },
        ),
        context=ToolContext(session_id="session-1"),
    )

    todos = [
        {"content": "Inspect tools", "status": "completed", "priority": "medium"},
        {"content": "Run tests", "status": "in_progress", "priority": "medium"},
        {"content": "Drop stale task", "status": "cancelled", "priority": "low"},
    ]
    assert result.status == "success"
    assert result.output == {"todos": todos}
    assert json.loads(result.content) == {"todos": todos}
    assert result.metadata["todos"] == todos
    assert result.metadata["todo_count"] == 3
    assert result.metadata["active_todo_count"] == 1
    assert result.metadata["completed_todo_count"] == 1
    assert result.metadata["cancelled_todo_count"] == 1

    todo_event = next(event for event in result.events if event.type == "todo.updated")
    assert todo_event.session_id == "session-1"
    assert todo_event.payload == {
        "tool_id": "todo_write",
        "tool_call_id": "call-todo",
        "todos": todos,
        "todo_count": 3,
        "active_todo_count": 1,
        "completed_todo_count": 1,
        "cancelled_todo_count": 1,
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

    invalid_priority = await runtime.execute(
        ToolCall(
            id="call-todo-invalid-priority",
            tool_id="todo_write",
            args={
                "todos": [
                    {
                        "content": "Bad priority",
                        "status": "pending",
                        "priority": "urgent",
                    }
                ]
            },
        )
    )

    assert invalid_priority.status == "validation_error"
    assert "todos[0].priority" in invalid_priority.error


@pytest.mark.asyncio
async def test_new_core_tool_permission_defaults(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path)
    assert registry.ids() == [
        "apply_patch",
        "bash",
        "edit",
        "fetch",
        "glob",
        "grep",
        "invalid",
        "list_dir",
        "read",
        "read_file",
        "shell_exec",
        "shell_kill",
        "shell_status",
        "todo_write",
        "todowrite",
        "webfetch",
        "write",
        "write_file",
    ]
    assert registry.require("glob").permission.action == ALLOW
    assert registry.require("fetch").permission.action == ALLOW
    assert registry.require("fetch").permission.category == "network"
    assert registry.require("fetch").permission.risk == "medium"
    assert registry.require("webfetch").permission.action == ALLOW
    assert registry.require("webfetch").permission.category == "network"
    assert registry.require("webfetch").permission.risk == "medium"
    assert registry.require("invalid").permission.action == ALLOW
    assert registry.require("invalid").permission.category == "validation"
    assert registry.require("todo_write").permission.action == ALLOW
    assert registry.require("todo_write").permission.category == "planning"
    assert registry.require("todo_write").permission.resource == "session"
    assert registry.require("todo_write").permission.risk == "low"
    assert registry.require("todowrite").permission.action == ALLOW
    assert registry.require("todowrite").permission.category == "planning"
    assert registry.require("todowrite").permission.resource == "session"
    assert registry.require("todowrite").permission.risk == "low"
    assert registry.require("shell_status").permission.action == ALLOW
    assert registry.require("shell_status").permission.risk == "low"
    assert registry.require("edit").permission.action == ASK
    assert registry.require("apply_patch").permission.action == ASK
    assert registry.require("bash").permission.action == ASK
    assert registry.require("bash").permission.category == "shell"
    assert registry.require("bash").permission.resource == "workspace"
    assert registry.require("bash").permission.risk == "high"
    assert registry.require("shell_exec").permission.action == ASK
    assert registry.require("shell_kill").permission.action == ASK
    assert registry.require("shell_kill").permission.risk == "medium"

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
