from __future__ import annotations

import asyncio
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionDecision, PermissionMetadata
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


def _runtime(tmp_path: Path) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.mark.asyncio
async def test_shell_schema_matches_opencode_visible_arguments(tmp_path: Path):
    schema = create_core_tool_registry(tmp_path).require("bash").input_schema

    assert schema["required"] == ["command", "description"]
    assert list(schema["properties"]) == [
        "command",
        "description",
        "timeout",
        "workdir",
    ]
    assert schema["properties"]["timeout"] == {"type": "integer", "minimum": 1}


@pytest.mark.asyncio
async def test_workdir_sets_workspace_relative_cwd(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-workdir",
            tool_id="bash",
            args={"command": "printf ok", "workdir": "pkg", "description": "Print ok"},
        )
    )

    assert result.status == "success"
    assert result.output["cwd"] == "pkg"
    assert result.metadata["cwd"] == "pkg"
    assert result.metadata["description"] == "Print ok"


@pytest.mark.asyncio
async def test_timeout_is_milliseconds_and_adds_shell_metadata(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-timeout",
            tool_id="bash",
            args={
                "command": _python_command("import time; time.sleep(1)"),
                "description": "Sleep longer than timeout",
                "timeout": 100,
            },
        )
    )

    assert result.status == "success"
    assert result.output["timed_out"] is True
    assert result.output["cancelled"] is False
    assert result.output["exit_code"] is None
    assert result.metadata["timed_out"] is True
    assert result.metadata["cancelled"] is False
    assert result.metadata["timeout_ms"] == 100
    assert "<shell_metadata>" in result.content
    assert "100ms" in result.content


@pytest.mark.asyncio
async def test_shell_command_cancellation_kills_process_and_preserves_output(tmp_path: Path):
    runtime = _runtime(tmp_path)
    cancel_requested = False

    async def flip_cancel() -> None:
        nonlocal cancel_requested
        await asyncio.sleep(0.1)
        cancel_requested = True

    async def is_cancelled() -> bool:
        return cancel_requested

    cancel_task = asyncio.create_task(flip_cancel())
    result = await asyncio.wait_for(
        runtime.execute(
            ToolCall(
                id="call-shell-cancel",
                tool_id="bash",
                args={
                    "command": _python_command(
                        "import sys, time; print('before'); sys.stdout.flush(); time.sleep(5)"
                    ),
                    "description": "Run cancellable command",
                },
            ),
            context=ToolContext(cancel_requested=is_cancelled),
        ),
        timeout=2,
    )
    await cancel_task

    assert result.status == "cancelled"
    assert result.success is False
    assert result.error == "Shell command cancelled."
    assert result.output["cancelled"] is True
    assert result.output["timed_out"] is False
    assert result.output["exit_code"] is None
    assert result.metadata["cancelled"] is True
    assert "before" in result.content
    assert "User aborted the command" in result.content


@pytest.mark.asyncio
async def test_stdout_and_stderr_are_both_visible_and_exit_code_is_kept(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-streams",
            tool_id="bash",
            args={
                "command": _python_command(
                    "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"
                ),
                "description": "Print stdout and stderr",
            },
        )
    )

    assert result.status == "success"
    assert result.output["stdout"] == "out\n"
    assert result.output["stderr"] == "err\n"
    assert result.output["exit_code"] == 7
    assert result.metadata["exit_code"] == 7
    assert "<stdout>\nout\n</stdout>" in result.content
    assert "<stderr>\nerr\n</stderr>" in result.content


@pytest.mark.asyncio
async def test_long_output_is_truncated_and_full_output_saved_inside_workspace(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-long-output",
            tool_id="bash",
            args={
                "command": _python_command(
                    "for i in range(250): print('line-%03d' % i)"
                ),
                "description": "Print many lines",
            },
        )
    )

    assert result.status == "success"
    assert result.truncated is True
    assert result.metadata["truncated"] is True
    assert result.content.startswith("...output truncated...")
    assert "line-249" in result.content
    assert "line-000" not in result.content

    output_path = result.metadata["output_path"]
    assert output_path.startswith(".efp_runtime/tool-output/")
    saved_path = (tmp_path / output_path).resolve()
    saved_path.relative_to(tmp_path.resolve())
    saved_content = saved_path.read_text(encoding="utf-8")
    assert "line-000" in saved_content
    assert "line-249" in saved_content


@pytest.mark.asyncio
async def test_cwd_alias_is_not_model_visible(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-cwd",
            tool_id="bash",
            args={"command": "printf ok", "description": "Print ok", "cwd": "."},
        )
    )

    assert result.status == "validation_error"
    assert "Unexpected argument(s): cwd" in result.error


@pytest.mark.asyncio
async def test_workdir_cannot_escape_workspace(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-escape",
            tool_id="bash",
            args={"command": "printf ok", "description": "Print ok", "workdir": "../"},
        )
    )

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_shell_permission_request_includes_usable_metadata(tmp_path: Path):
    (tmp_path / "src").mkdir()
    runtime = ToolRuntime(
        create_core_tool_registry(
            tmp_path,
            shell_permission=PermissionMetadata(
                action=ASK,
                reason="Shell execution requires approval.",
                category="shell",
                resource="workspace",
                risk="high",
                data={
                    "command_preview": "",
                    "description": "",
                    "workdir": ".",
                },
            ),
        )
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell-permission",
            tool_id="bash",
            args={
                "command": "printf ok",
                "description": "Print ok",
                "workdir": "src",
            },
        ),
        context=ToolContext(session_id="session-shell"),
    )

    request = result.metadata["permission_request"]
    assert request["category"] == "shell"
    assert request["resource"] == "workspace"
    assert request["risk"] == "high"
    assert request["reason"] == "Shell execution requires approval."
    assert request["metadata"]["command_preview"] == "printf ok"
    assert request["metadata"]["command_name"] == "printf"
    assert request["metadata"]["command_names"] == ["printf"]
    assert request["metadata"]["path_args"] == []
    assert request["metadata"]["description"] == "Print ok"
    assert request["metadata"]["workdir"] == "src"
    assert request["patterns"] == ["printf ok"]


def test_shell_tool_source_stays_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/tools/builtin/shell.py",
            ROOT / "src/efp_runtime/tools/builtin/output.py",
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "src.agents.core",
        "src.runtime",
        "src.bash_tools",
    ]

    for token in forbidden_tokens:
        assert token not in combined
