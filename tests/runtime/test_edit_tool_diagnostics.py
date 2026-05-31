from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall, ToolResult
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
async def test_edit_success_returns_tool_result_with_diff_diagnostics(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    tool = create_core_tool_registry(tmp_path).require("edit")

    result = await tool.execute(
        tool.validate_args(
            {"filePath": "notes.txt", "oldString": "beta", "newString": "gamma"}
        ),
        ToolContext(tool_call_id="call-edit"),
    )

    assert isinstance(result, ToolResult)
    assert result.status == "success"
    assert result.success is True
    assert result.output["path"] == "notes.txt"
    assert result.output["replacement_count"] == 1
    assert result.output["bytes"] == len("alpha\ngamma\n".encode("utf-8"))
    assert result.output["old_bytes"] == len("alpha\nbeta\n".encode("utf-8"))
    assert result.output["new_bytes"] == len("alpha\ngamma\n".encode("utf-8"))
    assert result.output["changed"] is True
    assert result.output["diff_truncated"] is False
    assert "--- a/notes.txt" in result.output["diff"]
    assert "+++ b/notes.txt" in result.output["diff"]
    assert "-beta" in result.output["diff"]
    assert "+gamma" in result.output["diff"]
    filediff = result.output["filediff"]
    assert result.metadata["filediff"] == filediff
    assert filediff["path"] == "notes.txt"
    assert filediff["old_path"] == "notes.txt"
    assert filediff["additions"] == 1
    assert filediff["deletions"] == 1
    assert filediff["patch"] == result.output["diff"]
    assert "Edited notes.txt" in result.content
    assert "replacement_count=1" in result.content
    assert "bytes=" in result.content
    assert "Diff preview:" in result.content
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


@pytest.mark.asyncio
async def test_edit_rejects_identical_old_and_new_string(tmp_path: Path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nalpha\n", encoding="utf-8")
    before = target.read_bytes()
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-edit",
            tool_id="edit",
            args={"filePath": "notes.txt", "oldString": "alpha", "newString": "alpha"},
        )
    )

    assert result.status == "error"
    assert "No changes to apply" in result.error
    assert target.read_bytes() == before


@pytest.mark.asyncio
async def test_edit_multiple_matches_error_includes_count_and_replace_all_hint(
    tmp_path: Path,
):
    target = tmp_path / "dup.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-edit",
            tool_id="edit",
            args={"filePath": "dup.txt", "oldString": "same", "newString": "diff"},
        )
    )

    assert result.status == "error"
    assert "2 times" in result.error
    assert "replaceAll=true" in result.error
    assert "more precise oldString" in result.error
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


@pytest.mark.asyncio
async def test_edit_missing_old_text_error_includes_path_preview_and_file_size(
    tmp_path: Path,
):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\n", encoding="utf-8")
    missing = "missing-" + ("x" * 200)
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-edit",
            tool_id="edit",
            args={"filePath": "notes.txt", "oldString": missing, "newString": "beta"},
        )
    )

    assert result.status == "error"
    assert "oldString was not found in notes.txt" in result.error
    assert "oldString preview: missing-" in result.error
    assert "file characters: 6" in result.error
    preview = result.error.split("oldString preview: ", 1)[1].split(
        ". file characters:",
        1,
    )[0]
    assert len(preview) <= 120
    assert target.read_text(encoding="utf-8") == "alpha\n"


@pytest.mark.asyncio
async def test_edit_empty_old_string_creates_or_overwrites_file(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    created = await runtime.execute(
        ToolCall(
            id="call-edit-create",
            tool_id="edit",
            args={"filePath": "nested/new.txt", "oldString": "", "newString": "created\n"},
        )
    )

    assert created.status == "success"
    assert created.output["path"] == "nested/new.txt"
    assert created.output["changed"] is True
    assert (tmp_path / "nested/new.txt").read_text(encoding="utf-8") == "created\n"

    overwritten = await runtime.execute(
        ToolCall(
            id="call-edit-overwrite",
            tool_id="edit",
            args={"filePath": "nested/new.txt", "oldString": "", "newString": "overwritten\n"},
        )
    )

    assert overwritten.status == "success"
    assert overwritten.output["old_bytes"] == len("created\n".encode("utf-8"))
    assert (tmp_path / "nested/new.txt").read_text(encoding="utf-8") == "overwritten\n"


@pytest.mark.asyncio
async def test_edit_rejects_model_visible_diff_preview_limits(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )
    target = tmp_path / "lines.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = await runtime.execute(
        ToolCall(
            id="call-edit-lines",
            tool_id="edit",
            args={
                "filePath": "lines.txt",
                "oldString": "two",
                "newString": "changed",
                "max_diff_lines": 3,
            },
        )
    )

    assert result.status == "validation_error"
    assert "Unexpected argument(s): max_diff_lines" in result.error
    assert target.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


@pytest.mark.asyncio
async def test_apply_patch_success_returns_paths_and_bounded_patch_preview(
    tmp_path: Path,
):
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )
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
        ToolCall(
            id="call-patch",
            tool_id="apply_patch",
            args={"patch": patch, "max_patch_preview_lines": 4},
        )
    )

    assert result.status == "success"
    assert result.output["ok"] is True
    assert result.output["paths"] == ["hello.txt"]
    assert result.output["changed_file_count"] == 1
    assert result.output["patch_preview"].startswith("diff --git")
    assert len(result.output["patch_preview"].splitlines()) <= 4
    assert result.output["patch_preview_truncated"] is True
    assert result.output["filediffs"] == [result.output["filediff"]]
    assert result.metadata["filediffs"] == result.output["filediffs"]
    assert result.metadata["filediff"] == result.output["filediff"]
    assert result.output["filediff"]["path"] == "hello.txt"
    assert result.output["filediff"]["old_path"] == "hello.txt"
    assert result.output["filediff"]["additions"] == 1
    assert result.output["filediff"]["deletions"] == 1
    assert len(result.output["filediff"]["patch"].splitlines()) <= 4
    assert "Changed paths: hello.txt" in result.content
    assert "Patch preview:" in result.content
    assert target.read_text(encoding="utf-8") == "patched\n"


@pytest.mark.asyncio
async def test_apply_patch_multi_file_returns_file_diff_records(tmp_path: Path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("two\n", encoding="utf-8")
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )
    patch = textwrap.dedent(
        """\
        diff --git a/first.txt b/first.txt
        --- a/first.txt
        +++ b/first.txt
        @@ -1 +1 @@
        -one
        +uno
        diff --git a/second.txt b/second.txt
        --- a/second.txt
        +++ b/second.txt
        @@ -1 +1,2 @@
        -two
        +dos
        +extra
        """
    )

    result = await runtime.execute(
        ToolCall(id="call-patch-multi", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "success"
    assert result.output["paths"] == ["first.txt", "second.txt"]
    assert "filediff" not in result.output
    assert "filediff" not in result.metadata
    assert result.metadata["filediffs"] == result.output["filediffs"]
    assert len(result.output["filediffs"]) == 2
    first_diff, second_diff = result.output["filediffs"]
    assert first_diff["path"] == "first.txt"
    assert first_diff["old_path"] == "first.txt"
    assert first_diff["additions"] == 1
    assert first_diff["deletions"] == 1
    assert "+uno" in first_diff["patch"]
    assert second_diff["path"] == "second.txt"
    assert second_diff["old_path"] == "second.txt"
    assert second_diff["additions"] == 2
    assert second_diff["deletions"] == 1
    assert "+extra" in second_diff["patch"]
    assert first.read_text(encoding="utf-8") == "uno\n"
    assert second.read_text(encoding="utf-8") == "dos\nextra\n"


@pytest.mark.asyncio
async def test_apply_patch_failure_content_includes_stderr_preview_and_paths(
    tmp_path: Path,
):
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )
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
        ToolCall(id="call-patch", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "error"
    assert result.success is False
    assert result.output["ok"] is False
    assert result.output["paths"] == ["hello.txt"]
    assert result.output["stderr_preview"]
    assert "Patch failed: Patch check failed." in result.content
    assert "Paths: hello.txt" in result.content
    assert "stderr preview:" in result.content
    assert result.output["stderr_preview"].strip() in result.content
    assert target.read_text(encoding="utf-8") == "hello\n"


@pytest.mark.asyncio
async def test_apply_patch_workspace_escape_still_rejected(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )
    patch = textwrap.dedent(
        """\
        --- /dev/null
        +++ b/../outside.txt
        @@ -0,0 +1 @@
        +outside
        """
    )

    result = await runtime.execute(
        ToolCall(id="call-patch", tool_id="apply_patch", args={"patch": patch})
    )

    assert result.status == "error"
    assert result.output["ok"] is False
    assert "Path escapes workspace root." in result.error
    assert "Path escapes workspace root." in result.content


def test_diagnostic_tool_sources_stay_inside_runtime_import_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/tools/builtin").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.sessions",
        "import src.sessions",
        "from src.agents.core",
        "import src.agents.core",
        "from src.runtime",
        "import src.runtime",
        "from src.skills",
        "import src.skills",
    ]
    for token in forbidden_tokens:
        assert token not in combined
