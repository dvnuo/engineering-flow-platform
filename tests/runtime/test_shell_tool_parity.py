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
from efp_runtime.tools.builtin import shell as shell_module
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
    timeout = schema["properties"]["timeout"]
    assert {key: timeout[key] for key in ("type", "minimum")} == {"type": "integer", "minimum": 1}


def test_the_model_is_told_the_unit_and_the_default_of_timeout(tmp_path: Path):
    """An unlabelled ``timeout`` was filled in as seconds: 120 stopped a command after 120 ms."""
    tool = create_core_tool_registry(tmp_path).require("bash")
    timeout = tool.input_schema["properties"]["timeout"]
    default = str(shell_module.DEFAULT_TIMEOUT_MS)

    assert shell_module.DEFAULT_TIMEOUT_MS == 120_000
    assert "milliseconds" in timeout["description"]
    assert default in timeout["description"]
    assert "below 1000 is read as seconds" in timeout["description"]
    assert "milliseconds" in tool.description
    assert default in tool.description


@pytest.mark.parametrize(
    ("given", "effective_ms"),
    [
        # Below a second nobody means milliseconds: seconds.
        (1, 1_000),
        (90, 90_000),
        (120, 120_000),
        (999, 999_000),
        # From 1000 on the value is milliseconds, as before.
        (1_000, 1_000),
        (5_000, 5_000),
        (120_000, 120_000),
        (600_000, 600_000),
    ],
)
def test_a_timeout_below_one_second_is_read_as_seconds(given: int, effective_ms: int):
    seconds, timeout_ms = shell_module._resolve_timeout({"timeout": given})

    assert timeout_ms == effective_ms
    assert seconds == effective_ms / 1000.0


def test_a_call_without_timeout_gets_two_minutes():
    assert shell_module._resolve_timeout({}) == (120.0, 120_000)


@pytest.mark.parametrize("bad", [0, -5, 1.5, "120", True, None])
def test_a_timeout_that_is_no_positive_integer_is_refused(bad: Any):
    with pytest.raises(ValueError):
        shell_module._resolve_timeout({"timeout": bad})


@pytest.mark.asyncio
async def test_a_timeout_written_in_seconds_lets_the_command_finish(tmp_path: Path):
    """``timeout: 120`` is two minutes, so a command that takes a moment is not cut at 120 ms."""
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-shell-seconds",
            tool_id="bash",
            args={
                "command": _python_command("import time; time.sleep(0.6); print('read')"),
                "description": "Takes longer than 120 ms",
                "timeout": 120,
            },
        )
    )

    assert result.status == "success"
    assert result.output["timed_out"] is False
    assert result.output["exit_code"] == 0
    assert "read" in result.output["stdout"]
    assert result.metadata["timeout_ms"] == 120_000
    assert "exceeding timeout" not in result.content


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
                "command": _python_command("import time; time.sleep(10)"),
                "description": "Sleep longer than timeout",
                "timeout": 1000,
            },
        )
    )

    assert result.status == "success"
    assert result.output["timed_out"] is True
    assert result.output["cancelled"] is False
    assert result.output["exit_code"] is None
    assert result.metadata["timed_out"] is True
    assert result.metadata["cancelled"] is False
    assert result.metadata["timeout_ms"] == 1000
    assert "<shell_metadata>" in result.content
    assert "exceeding timeout 1000ms (1 second)" in result.content
    # The model must not read a timeout as "the CLI is unavailable", and must
    # learn how to give the command longer.
    assert "ran out of time" in result.content
    assert "does not show that what it ran is broken or unavailable" in result.content
    assert "larger `timeout`, in milliseconds (300000 is five minutes)" in result.content


@pytest.mark.parametrize(
    ("timeout_ms", "text"),
    [
        (1_000, "1 second"),
        (1_500, "1.5 seconds"),
        (30_000, "30 seconds"),
        (120_000, "120 seconds"),
        (3_600_000, "3600 seconds"),
    ],
)
def test_the_timeout_note_gives_the_limit_in_both_units(timeout_ms: int, text: str):
    content = shell_module._format_shell_content(
        stdout="",
        stderr="",
        timed_out=True,
        cancelled=False,
        timeout_ms=timeout_ms,
    )

    assert f"exceeding timeout {timeout_ms}ms ({text})." in content


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


def test_shell_tool_source_stays_inside_runtime_boundary():
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
