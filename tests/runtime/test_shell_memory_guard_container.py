"""The shell tool's memory guard against a real container limit.

Everything in ``test_shell_memory_bounds.py`` feeds the guard made-up readings.
This file runs the tool where it runs in production: inside a Linux container
with a memory limit, reading the real cgroup and ``/proc`` files and stopping a
command that really is filling the container. It is skipped anywhere else; CI
starts the container for it (see ``.github/workflows/ci.yml``).
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import PermissionDecision, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.builtin.memory_guard import (
    read_container_memory,
    session_memory_bytes,
    session_process_ids,
)
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


GIB = 1024 * 1024 * 1024
MIB = 1024 * 1024


def _container_limit() -> int | None:
    if os.name != "posix":
        return None
    reading = read_container_memory()
    if reading is None or reading.limit_bytes > GIB:
        return None
    return reading.limit_bytes


# CI sets this where it starts the container. Without it, a guard that stopped
# reading the limit would skip the only tests that run it for real, and the
# run would stay green.
_REQUIRED = os.environ.get("EFP_TEST_REQUIRE_MEMORY_LIMIT") == "1"

pytestmark = pytest.mark.skipif(
    not _REQUIRED and _container_limit() is None,
    reason="needs a Linux container with a memory limit of 1 GiB or less",
)


@pytest.fixture(autouse=True)
def _no_stop_is_settling(monkeypatch: pytest.MonkeyPatch):
    from efp_runtime.tools.builtin import shell as shell_module

    monkeypatch.setattr(shell_module, "_memory_stop_settles_at", 0.0)

# Grows by 4 MiB every 100 ms and says how far it got. Slow enough that the
# guard's half-second poll sees it cross 90% well before the limit itself.
_FILL_MEMORY = (
    "import time\n"
    "blocks = []\n"
    "while True:\n"
    "    blocks.append(b'x' * (4 * 1024 * 1024))\n"
    "    print('holding', len(blocks) * 4, 'MiB', flush=True)\n"
    "    time.sleep(0.1)\n"
)


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


def test_the_container_limit_and_this_process_are_read_from_the_real_files():
    reading = read_container_memory()

    assert reading is not None, "no memory limit was read inside the container CI started"
    assert reading.limit_bytes == _container_limit()
    assert 0 < reading.used_bytes < reading.limit_bytes
    # This test process and whatever started it, counted from the real /proc:
    # memory of their own, without the files they have mapped.
    session = os.getsid(0)
    assert session_memory_bytes([session])[session] > 10 * MIB
    assert os.getpid() in session_process_ids(session)


@pytest.mark.asyncio
async def test_a_command_filling_the_container_is_stopped_and_the_runtime_lives_on(tmp_path: Path):
    limit = _container_limit()
    assert limit is not None, "no memory limit: filling memory here would take the machine down"
    runtime = _runtime(tmp_path)

    result = await runtime.execute(
        ToolCall(
            id="call-fill-memory",
            tool_id="bash",
            args={
                "command": _python_command(_FILL_MEMORY),
                "description": "Fill the container's memory",
                "timeout": 120_000,
            },
        )
    )

    # Reaching these lines at all is the point: without the guard the kernel
    # ends the container's processes, this one included.
    tail = result.content[-1500:]
    assert result.status == "success", tail
    assert result.output.get("memory_exceeded") is True, tail
    assert result.output["timed_out"] is False
    assert result.output["exit_code"] is None
    assert result.metadata["memory_limit_bytes"] == limit
    assert result.metadata["memory_used_bytes"] >= limit * 0.9
    assert result.metadata["command_memory_bytes"] >= limit * 0.1
    assert "holding" in result.content
    assert "before the assistant ran out of memory" in result.content
    assert "Only this command was stopped" in result.content

    # Its memory came back with it, and the next command runs normally.
    after = read_container_memory()
    assert after is not None and after.used_percent < 90
    following = await runtime.execute(
        ToolCall(
            id="call-after-fill",
            tool_id="bash",
            args={"command": "echo still here", "description": "Run after the stop"},
        )
    )
    assert following.output["stdout"] == "still here\n"
    assert following.output["exit_code"] == 0
    assert "memory_exceeded" not in following.output


@pytest.mark.asyncio
async def test_a_command_run_through_timeout_is_stopped_as_well(tmp_path: Path):
    # `timeout` moves into a process group of its own, and what it runs goes
    # with it, out of the group the tool kills first. `; true` keeps the shell
    # from replacing itself with `timeout`, which would leave one group again.
    limit = _container_limit()
    assert limit is not None, "no memory limit: filling memory here would take the machine down"
    runtime = _runtime(tmp_path)
    says_its_group = (
        "import os\n"
        "print('outside the group of the shell:', os.getpgrp() != os.getsid(0), flush=True)\n"
    )

    result = await runtime.execute(
        ToolCall(
            id="call-fill-memory-under-timeout",
            tool_id="bash",
            args={
                "command": "timeout 110 " + _python_command(says_its_group + _FILL_MEMORY) + "; true",
                "description": "Fill the container's memory under timeout",
                "timeout": 120_000,
            },
        )
    )

    # Reaching these lines at all means the process holding the memory was
    # stopped: killing only the shell's group would have left it filling the
    # container until the kernel ended everything in it.
    tail = result.content[-1500:]
    assert result.status == "success", tail
    assert result.output.get("memory_exceeded") is True, tail
    assert result.output["timed_out"] is False
    assert result.metadata["command_memory_bytes"] >= limit * 0.1
    saved = (tmp_path / result.metadata["output_path"]).read_text(encoding="utf-8")
    assert "outside the group of the shell: True" in saved
    assert "holding" in saved
    after = read_container_memory()
    assert after is not None and after.used_percent < 90
