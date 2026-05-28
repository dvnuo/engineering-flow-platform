from __future__ import annotations

import os
from pathlib import Path

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_grep_include_filters_files_relative_to_search_root(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("needle py\n", encoding="utf-8")
    (src / "app.txt").write_text("needle txt\n", encoding="utf-8")
    (src / "view.ts").write_text("needle ts\n", encoding="utf-8")
    (src / "view.tsx").write_text("needle tsx\n", encoding="utf-8")
    (src / "view.js").write_text("needle js\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    py_result = await runtime.execute(
        ToolCall(
            id="call-grep-py",
            tool_id="grep",
            args={"pattern": "needle", "path": "src", "include": "*.py"},
        )
    )

    assert py_result.status == "success"
    assert [match["path"] for match in py_result.output["matches"]] == ["src/app.py"]
    assert py_result.output["files_searched"] == 1
    assert py_result.output["include"] == "*.py"
    assert py_result.content.startswith("Found 1 matches\nsrc/app.py:")

    ts_result = await runtime.execute(
        ToolCall(
            id="call-grep-brace",
            tool_id="grep",
            args={"pattern": "needle", "path": "src", "include": "*.{ts,tsx}"},
        )
    )

    assert ts_result.status == "success"
    assert {match["path"] for match in ts_result.output["matches"]} == {
        "src/view.ts",
        "src/view.tsx",
    }
    assert ts_result.output["files_searched"] == 2
    assert ts_result.output["total_matches"] == 2


@pytest.mark.asyncio
async def test_grep_content_is_readable_recent_first_and_bounded(tmp_path: Path):
    recent = tmp_path / "recent.py"
    middle = tmp_path / "middle.py"
    old = tmp_path / "old.py"
    recent.write_text("needle " + "x" * 2105 + "\n", encoding="utf-8")
    middle.write_text("needle middle\n", encoding="utf-8")
    old.write_text("needle old\n", encoding="utf-8")
    os.utime(old, (100, 100))
    os.utime(middle, (200, 200))
    os.utime(recent, (300, 300))
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-grep-readable",
            tool_id="grep",
            args={"pattern": "needle", "path": ".", "max_matches": 2},
        )
    )

    assert result.status == "success"
    assert result.output["total_matches"] == 3
    assert result.output["returned_matches"] == 2
    assert result.output["truncated"] is True
    assert result.output["matches"][0]["path"] == "recent.py"
    assert result.output["matches"][1]["path"] == "middle.py"
    assert result.content.startswith("Found 3 matches (showing first 2)")
    assert "recent.py:\n  Line 1: " in result.content
    assert "middle.py:\n  Line 1: needle middle" in result.content
    assert "old.py" not in result.content
    assert "Results truncated: showing 2 of 3 matches" in result.content
    assert "x" * 2100 not in result.content
    assert result.output["matches"][0]["line"].endswith("...")


@pytest.mark.asyncio
async def test_grep_no_matches_returns_no_files_found(tmp_path: Path):
    (tmp_path / "app.py").write_text("haystack\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-grep-empty", tool_id="grep", args={"pattern": "needle"})
    )

    assert result.status == "success"
    assert result.output["matches"] == []
    assert result.output["total_matches"] == 0
    assert result.content == "No files found"


@pytest.mark.asyncio
async def test_search_includes_hidden_files_and_excludes_git(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.txt").write_text("needle git\n", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("needle hidden\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("needle visible\n", encoding="utf-8")
    os.utime(tmp_path / ".hidden.txt", (200, 200))
    os.utime(tmp_path / "visible.txt", (100, 100))
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    grep_result = await runtime.execute(
        ToolCall(id="call-grep-hidden", tool_id="grep", args={"pattern": "needle"})
    )

    assert grep_result.status == "success"
    assert [match["path"] for match in grep_result.output["matches"]] == [
        ".hidden.txt",
        "visible.txt",
    ]
    assert grep_result.output["total_matches"] == 2
    assert grep_result.output["files_searched"] == 2
    assert ".git" not in grep_result.content

    glob_result = await runtime.execute(
        ToolCall(id="call-glob-hidden", tool_id="glob", args={"pattern": "*.txt"})
    )

    assert glob_result.status == "success"
    assert glob_result.output["paths"] == [".hidden.txt", "visible.txt"]
    assert all(".git" not in path for path in glob_result.output["paths"])


@pytest.mark.asyncio
async def test_search_fallback_is_safe_for_invalid_utf8_and_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PATH", "")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "ignored.txt").write_text("needle git\n", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("needle hidden\n", encoding="utf-8")
    (tmp_path / "bad.txt").write_bytes(b"bad \xff needle\n")
    os.utime(tmp_path / ".hidden.txt", (100, 100))
    os.utime(tmp_path / "bad.txt", (200, 200))
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    grep_result = await runtime.execute(
        ToolCall(id="call-grep-fallback", tool_id="grep", args={"pattern": "needle"})
    )

    assert grep_result.status == "success"
    assert [match["path"] for match in grep_result.output["matches"]] == [
        "bad.txt",
        ".hidden.txt",
    ]
    assert grep_result.output["matches"][0]["line"] == "bad \ufffd needle"
    assert grep_result.output["files_searched"] == 2
    assert ".git" not in grep_result.content

    glob_result = await runtime.execute(
        ToolCall(id="call-glob-fallback", tool_id="glob", args={"pattern": "*.txt"})
    )

    assert glob_result.status == "success"
    assert glob_result.output["paths"] == ["bad.txt", ".hidden.txt"]
    assert all(".git" not in path for path in glob_result.output["paths"])


@pytest.mark.asyncio
async def test_glob_defaults_to_100_matches_and_sorts_by_mtime(tmp_path: Path):
    for index in range(105):
        path = tmp_path / f"file-{index:03}.txt"
        path.write_text(f"{index}\n", encoding="utf-8")
        os.utime(path, (1000 + index, 1000 + index))
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-glob-default", tool_id="glob", args={"pattern": "*.txt"})
    )

    assert result.status == "success"
    assert len(result.output["paths"]) == 100
    assert result.output["paths"][:3] == [
        "file-104.txt",
        "file-103.txt",
        "file-102.txt",
    ]
    assert result.output["paths"][-1] == "file-005.txt"
    assert result.output["truncated"] is True
    assert result.metadata["total_matches"] == 105
    assert result.metadata["returned_matches"] == 100
    assert result.content.splitlines()[0] == "file-104.txt"
    assert "Results are truncated" in result.content


@pytest.mark.asyncio
async def test_glob_no_matches_returns_no_files_found(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-glob-empty", tool_id="glob", args={"pattern": "*.py"})
    )

    assert result.status == "success"
    assert result.output["paths"] == []
    assert result.content == "No files found"
