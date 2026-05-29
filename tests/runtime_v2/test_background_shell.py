from __future__ import annotations

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


def _runtime(tmp_path: Path, *, enable_background_shell: bool = True) -> ToolRuntime:
    return ToolRuntime(
        create_core_tool_registry(
            tmp_path,
            enable_background_shell=enable_background_shell,
        ),
        permission_evaluator=AllowEvaluator(),
    )


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.mark.asyncio
async def test_bash_background_returns_job_without_polling_tools(tmp_path: Path):
    runtime = _runtime(tmp_path)
    started = time.monotonic()

    result = await runtime.execute(
        ToolCall(
            id="call-background-start",
            tool_id="bash",
            args={
                "command": _python_command("import time; time.sleep(0.05)"),
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
    assert result.metadata["background"] is True
    assert "shell_status" not in runtime.registry.ids()
    assert "shell_kill" not in runtime.registry.ids()


@pytest.mark.asyncio
async def test_background_shell_can_be_disabled_for_bash(tmp_path: Path):
    runtime = _runtime(tmp_path, enable_background_shell=False)

    result = await runtime.execute(
        ToolCall(
            id="call-background-disabled",
            tool_id="bash",
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
            max_iterations=1,
        ),
    )

    assert runtime.config.enable_background_shell is False
    assert runtime.config.background_shell_max_buffer_bytes == 4096
    assert "bash" in runtime.tool_runtime.registry.ids()
    assert "shell_status" not in runtime.tool_runtime.registry.ids()
    assert "shell_kill" not in runtime.tool_runtime.registry.ids()


@pytest.mark.asyncio
async def test_plan_mode_hides_bash_and_build_mode_exposes_it(tmp_path: Path):
    plan_provider = ScriptedLLMProvider([{"content": "planned"}])
    plan_runtime = AgentRuntime(
        provider=plan_provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            runtime_mode="plan",
            max_iterations=1,
        ),
    )

    plan_result = await plan_runtime.run("Plan.", session_id="session-background-plan")

    assert plan_result.status == LoopStatus.COMPLETED
    plan_tool_ids = [tool.id for tool in plan_provider.requests[0].tools]
    assert "bash" not in plan_tool_ids

    build_provider = ScriptedLLMProvider([{"content": "built"}])
    build_runtime = AgentRuntime(
        provider=build_provider,
        config=RuntimeConfig(workspace_root=tmp_path, max_iterations=1),
    )

    build_result = await build_runtime.run("Build.", session_id="session-background-build")

    assert build_result.status == LoopStatus.COMPLETED
    build_tool_ids = [tool.id for tool in build_provider.requests[0].tools]
    assert "bash" in build_tool_ids
    assert "shell_status" not in build_tool_ids
    assert "shell_kill" not in build_tool_ids


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
