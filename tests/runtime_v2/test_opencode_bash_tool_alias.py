from __future__ import annotations

import asyncio
import shlex
import sys
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


def test_core_registry_defaults_to_bash_without_legacy_shell_tools(tmp_path: Path):
    registry = create_core_tool_registry(tmp_path, enable_background_shell=True)
    ids = registry.ids()

    assert "bash" in ids
    assert {"shell_exec", "shell_status", "shell_kill"}.isdisjoint(ids)
    assert registry.require("bash").input_schema["additionalProperties"] is False
    assert registry.require("bash").permission.category == "shell"
    assert registry.require("bash").permission.resource == "workspace"
    assert registry.require("bash").permission.risk == "high"


def test_core_registry_can_include_legacy_shell_tools(tmp_path: Path):
    registry = create_core_tool_registry(
        tmp_path,
        enable_background_shell=True,
        include_legacy_aliases=True,
    )
    ids = registry.ids()

    assert {"bash", "shell_exec", "shell_status", "shell_kill"}.issubset(ids)
    assert registry.require("bash").input_schema == registry.require("shell_exec").input_schema
    assert registry.require("bash").input_schema["additionalProperties"] is False
    assert registry.require("bash").permission.category == "shell"
    assert registry.require("bash").permission.resource == "workspace"
    assert registry.require("bash").permission.risk == "high"


@pytest.mark.asyncio
async def test_bash_foreground_runs_and_returns_bash_result(tmp_path: Path):
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-bash-foreground",
            tool_id="bash",
            args={
                "command": _python_command(
                    "import sys; print('out'); print('err', file=sys.stderr)"
                ),
                "description": "write both streams",
            },
        ),
        context=ToolContext(session_id="session-bash", run_id="run-bash"),
    )

    assert result.status == "success"
    assert result.tool_name == "bash"
    assert result.output["stdout"] == "out\n"
    assert result.output["stderr"] == "err\n"
    assert result.output["exit_code"] == 0
    assert result.output["timed_out"] is False
    assert result.metadata["tool_call_id"] == "call-bash-foreground"
    assert result.metadata["run_id"] == "run-bash"
    assert result.metadata["cwd"] == "."
    assert result.metadata["description"] == "write both streams"
    assert result.metadata["stdout_chars"] == len("out\n")
    assert result.metadata["stderr_chars"] == len("err\n")
    assert result.metadata["output_path"].startswith(".efp_runtime/tool-output/")
    assert "<stdout>\nout\n</stdout>" in result.content
    assert "<stderr>\nerr\n</stderr>" in result.content


@pytest.mark.asyncio
async def test_bash_honors_cwd_workdir_and_rejects_conflict(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "other").mkdir()
    runtime = _runtime(tmp_path)

    cwd_result = await runtime.execute(
        ToolCall(
            id="call-bash-cwd",
            tool_id="bash",
            args={"command": "printf ok", "cwd": "pkg"},
        )
    )
    workdir_result = await runtime.execute(
        ToolCall(
            id="call-bash-workdir",
            tool_id="bash",
            args={"command": "printf ok", "workdir": "pkg"},
        )
    )
    conflict = await runtime.execute(
        ToolCall(
            id="call-bash-conflict",
            tool_id="bash",
            args={"command": "printf ok", "cwd": "pkg", "workdir": "other"},
        )
    )

    assert cwd_result.status == "success"
    assert cwd_result.output["cwd"] == "pkg"
    assert workdir_result.status == "success"
    assert workdir_result.output["cwd"] == "pkg"
    assert conflict.status == "error"
    assert "cwd and workdir" in conflict.error


@pytest.mark.asyncio
async def test_bash_timeout_matches_shell_exec(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_legacy_aliases=True),
        permission_evaluator=AllowEvaluator(),
    )
    args = {
        "command": _python_command("import time; time.sleep(1)"),
        "timeout": 5,
        "timeout_ms": 100,
    }

    bash = await runtime.execute(
        ToolCall(id="call-bash-timeout", tool_id="bash", args=args)
    )
    shell_exec = await runtime.execute(
        ToolCall(id="call-shell-timeout", tool_id="shell_exec", args=args)
    )

    for result in (bash, shell_exec):
        assert result.status == "success"
        assert result.output["timed_out"] is True
        assert result.output["cancelled"] is False
        assert result.output["exit_code"] is None
        assert result.metadata["timed_out"] is True
        assert result.metadata["cancelled"] is False
        assert result.metadata["timeout_ms"] == 100
        assert "<shell_metadata>" in result.content
        assert "100ms" in result.content
    assert bash.tool_name == "bash"
    assert shell_exec.tool_name == "shell_exec"


@pytest.mark.asyncio
async def test_bash_background_starts_job_readable_by_shell_status(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path, include_legacy_aliases=True),
        permission_evaluator=AllowEvaluator(),
    )

    start = await runtime.execute(
        ToolCall(
            id="call-bash-background",
            tool_id="bash",
            args={
                "command": _python_command("print('from background', flush=True)"),
                "background": True,
                "description": "background echo",
            },
        )
    )
    status = await _wait_for_finished(runtime, start.output["job_id"])

    assert start.status == "success"
    assert start.tool_name == "bash"
    assert start.metadata["background"] is True
    assert start.metadata["job_id"] == start.output["job_id"]
    assert start.metadata["description"] == "background echo"
    assert status.status == "success"
    assert status.tool_name == "shell_status"
    assert status.output["status"] == "exited"
    assert status.output["stdout"] == "from background\n"


@pytest.mark.asyncio
async def test_permission_config_can_deny_bash_without_hiding_shell_exec(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_legacy_tool_aliases=True,
            tool_permissions={"bash": "deny", "shell_exec": "allow"},
        ),
    )

    result = await runtime.run("List tools.", session_id="session-bash-permission")
    bash = await _execute_tool(runtime, "bash", {"command": "printf blocked"})
    shell_exec = await _execute_tool(
        runtime,
        "shell_exec",
        {"command": "printf allowed"},
    )
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    assert "bash" in schema_ids
    assert "shell_exec" in schema_ids
    assert bash.status == "permission_denied"
    assert bash.error == "Permission denied by runtime config: bash"
    assert shell_exec.status == "success"
    assert shell_exec.output["stdout"] == "allowed"


@pytest.mark.asyncio
async def test_agent_runtime_provider_schemas_include_bash_for_workspace(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    result = await runtime.run("Use tools.", session_id="session-bash-schema")
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    assert "bash" in schema_ids
    assert "shell_exec" not in schema_ids


@pytest.mark.asyncio
async def test_agent_runtime_provider_schemas_can_include_legacy_shell_exec(
    tmp_path: Path,
):
    provider = ScriptedLLMProvider([{"content": "done"}])
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=1,
            include_legacy_tool_aliases=True,
        ),
    )

    result = await runtime.run("Use tools.", session_id="session-bash-legacy-schema")
    schema_ids = [schema.id for schema in provider.requests[0].provider_request.tools]

    assert result.status == LoopStatus.COMPLETED
    assert "bash" in schema_ids
    assert "shell_exec" in schema_ids


async def _execute_tool(
    runtime: AgentRuntime,
    tool_id: str,
    args: dict[str, Any],
):
    return await runtime.tool_runtime.execute(
        ToolCall(id=f"call-{tool_id}", tool_id=tool_id, args=args),
        context=ToolContext(session_id=f"session-{tool_id}"),
    )


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
