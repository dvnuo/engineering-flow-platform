from __future__ import annotations

import asyncio
import shlex
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
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


def _runtime(
    tmp_path: Path,
    *,
    enable_background_shell: bool = True,
) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(
            tmp_path,
            enable_background_shell=enable_background_shell,
            include_legacy_aliases=True,
        ),
        permission_evaluator=AllowEvaluator(),
    )


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.mark.asyncio
async def test_shell_exec_background_returns_job_without_waiting(tmp_path: Path):
    runtime = _runtime(tmp_path)
    started = time.monotonic()

    result = await runtime.execute(
        ToolCall(
            id="call-background-start",
            tool_id="shell_exec",
            args={
                "command": _python_command("import time; time.sleep(2)"),
                "background": True,
                "description": "sleep in background",
            },
        )
    )
    duration = time.monotonic() - started

    assert result.status == "success"
    assert duration < 0.5
    assert result.output["job_id"].startswith("job_")
    assert result.output["status"] in {"running", "exited"}
    assert result.output["cwd"] == "."
    assert result.metadata["background"] is True
    assert result.metadata["job_id"] == result.output["job_id"]
    assert "job_id:" in result.content

    await runtime.execute(
        ToolCall(
            id="call-background-cleanup",
            tool_id="shell_kill",
            args={"job_id": result.output["job_id"]},
        )
    )


@pytest.mark.asyncio
async def test_shell_status_reads_streams_and_exit_code(tmp_path: Path):
    runtime = _runtime(tmp_path)
    start = await runtime.execute(
        ToolCall(
            id="call-background-streams",
            tool_id="shell_exec",
            args={
                "command": _python_command(
                    "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"
                ),
                "background": True,
            },
        )
    )

    status = await _wait_for_finished(runtime, start.output["job_id"])

    assert status.status == "success"
    assert status.output["status"] == "exited"
    assert status.output["exit_code"] == 7
    assert status.output["timed_out"] is False
    assert status.output["killed"] is False
    assert status.output["stdout"] == "out\n"
    assert status.output["stderr"] == "err\n"
    assert "<stdout>\nout\n</stdout>" in status.content
    assert "<stderr>\nerr\n</stderr>" in status.content


@pytest.mark.asyncio
async def test_shell_status_supports_offset_and_limit(tmp_path: Path):
    runtime = _runtime(tmp_path)
    start = await runtime.execute(
        ToolCall(
            id="call-background-offset-start",
            tool_id="shell_exec",
            args={
                "command": _python_command("import sys; sys.stdout.write('abcdef')"),
                "background": True,
            },
        )
    )
    await _wait_for_finished(runtime, start.output["job_id"])

    first = await runtime.execute(
        ToolCall(
            id="call-background-offset-first",
            tool_id="shell_status",
            args={"job_id": start.output["job_id"], "offset": 0, "limit": 3},
        )
    )
    second = await runtime.execute(
        ToolCall(
            id="call-background-offset-second",
            tool_id="shell_status",
            args={"job_id": start.output["job_id"], "offset": 3, "limit": 3},
        )
    )

    assert first.output["stdout"] == "abc"
    assert first.output["has_more"] is True
    assert first.output["next_offset"] == 3
    assert second.output["stdout"] == "def"
    assert second.output["has_more"] is False
    assert second.output["next_offset"] == 6


@pytest.mark.asyncio
async def test_shell_kill_stops_running_job(tmp_path: Path):
    runtime = _runtime(tmp_path)
    start = await runtime.execute(
        ToolCall(
            id="call-background-kill-start",
            tool_id="shell_exec",
            args={
                "command": _python_command(
                    "import time; print('ready', flush=True); time.sleep(30)"
                ),
                "background": True,
            },
        )
    )
    job_id = start.output["job_id"]

    kill = await runtime.execute(
        ToolCall(
            id="call-background-kill",
            tool_id="shell_kill",
            args={"job_id": job_id},
        )
    )
    status = await runtime.execute(
        ToolCall(
            id="call-background-kill-status",
            tool_id="shell_status",
            args={"job_id": job_id},
        )
    )

    assert kill.status == "success"
    assert kill.output["status"] == "killed"
    assert status.output["status"] in {"killed", "exited"}
    assert status.output["killed"] is (status.output["status"] == "killed")


@pytest.mark.asyncio
async def test_unknown_job_id_returns_tool_error(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-background-unknown",
            tool_id="shell_status",
            args={"job_id": "job_missing"},
        )
    )

    assert result.status == "error"
    assert result.success is False
    assert "Unknown background shell job" in result.error


@pytest.mark.asyncio
async def test_background_shell_can_be_disabled(tmp_path: Path):
    runtime = _runtime(tmp_path, enable_background_shell=False)

    assert "shell_status" not in runtime.registry.ids()
    assert "shell_kill" not in runtime.registry.ids()

    result = await runtime.execute(
        ToolCall(
            id="call-background-disabled",
            tool_id="shell_exec",
            args={"command": "printf ok", "background": True},
        )
    )

    assert result.status == "error"
    assert "Background shell jobs are disabled." in result.error


def test_agent_runtime_config_can_disable_background_shell(tmp_path: Path):
    runtime = AgentRuntime(
        provider=ScriptedLLMProvider([{"content": "unused"}]),
        config=RuntimeConfig(
            workspace_root=tmp_path,
            enable_background_shell=False,
            background_shell_max_buffer_bytes=4096,
            include_legacy_tool_aliases=True,
            max_iterations=1,
        ),
    )

    assert runtime.config.enable_background_shell is False
    assert runtime.config.background_shell_max_buffer_bytes == 4096
    assert "shell_exec" in runtime.tool_runtime.registry.ids()
    assert "bash" in runtime.tool_runtime.registry.ids()
    assert "shell_status" not in runtime.tool_runtime.registry.ids()
    assert "shell_kill" not in runtime.tool_runtime.registry.ids()


@pytest.mark.asyncio
async def test_plan_mode_hides_shell_entrypoints_but_build_mode_exposes_them(tmp_path: Path):
    plan_provider = ScriptedLLMProvider([{"content": "planned"}])
    plan_runtime = AgentRuntime(
        provider=plan_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            include_legacy_tool_aliases=True,
            max_iterations=1,
        ),
    )

    plan_result = await plan_runtime.run("Plan.", session_id="session-background-plan")

    assert plan_result.status == LoopStatus.COMPLETED
    plan_tool_ids = [tool.id for tool in plan_provider.requests[0].tools]
    assert "bash" not in plan_tool_ids
    assert "shell_exec" not in plan_tool_ids
    assert "shell_status" in plan_tool_ids

    build_provider = ScriptedLLMProvider([{"content": "built"}])
    build_runtime = AgentRuntime(
        provider=build_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            include_legacy_tool_aliases=True,
            max_iterations=1,
        ),
    )

    build_result = await build_runtime.run("Build.", session_id="session-background-build")

    assert build_result.status == LoopStatus.COMPLETED
    build_tool_ids = [tool.id for tool in build_provider.requests[0].tools]
    assert "bash" in build_tool_ids
    assert "shell_exec" in build_tool_ids
    assert "shell_status" in build_tool_ids
    assert "shell_kill" in build_tool_ids


def test_background_shell_import_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "src/efp_runtime/tools/builtin/background_shell.py",
            ROOT / "src/efp_runtime/tools/builtin/shell.py",
            ROOT / "src/efp_runtime/tools/builtin/registry.py",
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "src.sessions",
        "src.agents.core",
        "src.runtime",
        "src.skills",
    ]

    for token in forbidden_tokens:
        assert token not in combined


async def _wait_for_finished(runtime: ToolRuntime, job_id: str):
    for _ in range(80):
        result = await runtime.execute(
            ToolCall(
                id=f"call-status-{job_id}",
                tool_id="shell_status",
                args={"job_id": job_id},
            )
        )
        assert result.status == "success"
        if result.output["status"] != "running":
            return result
        await asyncio.sleep(0.05)
    raise AssertionError(f"job did not finish: {job_id}")
