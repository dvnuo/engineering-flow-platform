from __future__ import annotations

from pathlib import Path

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.shell_permissions import (
    shell_permission_metadata,
    shell_permission_patterns,
)
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


@pytest.mark.asyncio
async def test_bash_permission_request_extracts_cat_path(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-bash-cat",
            tool_id="bash",
            args={"command": "cat src/app.py"},
        ),
        context=ToolContext(session_id="session-bash-cat"),
    )

    request = result.metadata["permission_request"]
    metadata = request["metadata"]
    assert result.status == "permission_requested"
    assert metadata["command_name"] == "cat"
    assert metadata["path_args"][0]["path"] == "src/app.py"
    assert "src/app.py" in request["patterns"]
    assert "src/app.py" in metadata["permission_patterns"]


@pytest.mark.asyncio
async def test_bash_permission_request_applies_workdir_to_paths(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-bash-workdir",
            tool_id="bash",
            args={"command": "cat app.py", "workdir": "src"},
        ),
        context=ToolContext(session_id="session-bash-workdir"),
    )

    request = result.metadata["permission_request"]
    metadata = request["metadata"]
    assert result.status == "permission_requested"
    assert metadata["workdir"] == "src"
    assert metadata["path_args"][0]["path"] == "src/app.py"
    assert request["patterns"] == ["src/app.py"]


def test_shell_permission_scan_marks_rm_globs():
    metadata = shell_permission_metadata({"command": "rm -rf build dist/*.tmp"})

    assert metadata["command_name"] == "rm"
    assert metadata["permission_patterns"] == ["build", "dist/*.tmp"]
    assert metadata["path_args"][1]["path"] == "dist/*.tmp"
    assert metadata["path_args"][1]["glob"] is True


def test_shell_permission_scan_keeps_copy_and_move_sources_destinations():
    cp_metadata = shell_permission_metadata({"command": "cp src/a.py dst/a.py"})
    mv_metadata = shell_permission_metadata({"command": "mv old new"})

    assert [(entry["path"], entry["kind"]) for entry in cp_metadata["path_args"]] == [
        ("src/a.py", "source"),
        ("dst/a.py", "destination"),
    ]
    assert [(entry["path"], entry["kind"]) for entry in mv_metadata["path_args"]] == [
        ("old", "source"),
        ("new", "destination"),
    ]


@pytest.mark.parametrize(
    "command",
    [
        'rm "$TARGET"',
        "cat $(pwd)/file",
    ],
)
def test_shell_permission_scan_marks_dynamic_paths(command: str):
    metadata = shell_permission_metadata({"command": command})

    assert metadata["dynamic_paths"] is True
    assert metadata["path_args"][0]["dynamic"] is True
    assert metadata["permission_patterns"]


def test_shell_permission_scan_marks_cd_workspace_escape():
    metadata = shell_permission_metadata({"command": "cd .."})

    assert metadata["command_name"] == "cd"
    assert metadata["cwd_affecting"] is True
    assert metadata["workspace_escape"] is True
    assert metadata["permission_patterns"] == [".."]


def test_shell_permission_scan_parse_failure_falls_back_to_command_pattern():
    patterns = shell_permission_patterns({"command": "cat 'unterminated"})
    metadata = shell_permission_metadata({"command": "cat 'unterminated"})

    assert patterns == ["cat 'unterminated"]
    assert metadata["permission_patterns"] == ["cat 'unterminated"]
    assert metadata["path_args"] == []
    assert "shell_parse_error" in metadata


@pytest.mark.asyncio
async def test_bash_permission_request_uses_same_scan(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path)
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell-exec-cat",
            tool_id="bash",
            args={"command": "cat legacy.py"},
        ),
        context=ToolContext(session_id="session-shell-exec-cat"),
    )

    request = result.metadata["permission_request"]
    metadata = request["metadata"]
    assert result.status == "permission_requested"
    assert metadata["command_name"] == "cat"
    assert metadata["path_args"][0]["path"] == "legacy.py"
    assert request["patterns"] == ["legacy.py"]
