from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.events import RuntimeEvent
from efp_runtime.loop import LoopStatus, ScriptedLLMProvider
from efp_runtime.runtime import (
    AgentRuntime,
    RuntimeConfig,
    RuntimeRunState,
    SessionBusyError,
)
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


def _python_command(script: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.mark.asyncio
async def test_event_bus_publish_history_and_subscribe():
    bus = RuntimeEventBus()
    subscription = bus.subscribe()
    session_subscription = bus.subscribe(session_id="session-bus")

    event = RuntimeEvent(type="run_start", session_id="session-bus")
    other_event = RuntimeEvent(type="run_start", session_id="session-other")

    assert bus.publish(event) is event
    bus.publish(other_event)

    assert bus.history() == [event, other_event]
    assert bus.history("session-bus") == [event]
    assert bus.history("missing") == []
    assert await asyncio.wait_for(subscription.get(), timeout=1) is event
    assert await asyncio.wait_for(subscription.get(), timeout=1) is other_event
    assert await asyncio.wait_for(session_subscription.get(), timeout=1) is event

    subscription.close()
    session_subscription.close()


def test_run_state_rejects_concurrent_begin_for_same_session():
    state = RuntimeRunState()
    run_id = state.begin("session-busy")

    with pytest.raises(SessionBusyError) as exc_info:
        state.begin("session-busy")

    assert exc_info.value.session_id == "session-busy"
    assert exc_info.value.run_id == run_id
    assert state.cancel("session-busy") is True
    assert state.is_cancelled("session-busy") is True

    finished = state.finish("session-busy", LoopStatus.CANCELLED)

    assert finished is not None
    assert finished.status == LoopStatus.CANCELLED
    assert finished.active is False
    assert state.is_cancelled("session-busy") is False
    assert state.begin("session-busy") != run_id


@pytest.mark.asyncio
async def test_agent_runtime_rejects_concurrent_runs_for_same_session():
    started = asyncio.Event()
    release = asyncio.Event()
    bus = RuntimeEventBus()

    class BlockingProvider:
        async def invoke(self, request):
            started.set()
            await release.wait()
            return {"content": "Done."}

    runtime = AgentRuntime(
        provider=BlockingProvider(),
        tool_runtime=ToolRuntime(ToolRegistry()),
        event_bus=bus,
    )

    task = asyncio.create_task(runtime.run("First request.", session_id="session-facade-busy"))
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(SessionBusyError):
        await runtime.run("Second request.", session_id="session-facade-busy")

    release.set()
    result = await asyncio.wait_for(task, timeout=1)

    assert result.status == LoopStatus.COMPLETED
    event_types = [event.type for event in bus.history("session-facade-busy")]
    assert "run_start" in event_types
    assert "run_finish" in event_types


@pytest.mark.asyncio
async def test_agent_runtime_cancel_stops_before_next_provider_round():
    bus = RuntimeEventBus()

    async def request_cancel(args, context):
        runtime.cancel(context.session_id)
        return "cancel requested"

    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_cancel",
                        "type": "function",
                        "function": {"name": "request_cancel", "arguments": "{}"},
                    }
                ]
            },
            {"content": "This should not be requested."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        tool_runtime=ToolRuntime(
            ToolRegistry(
                [
                    ToolDef(
                        id="request_cancel",
                        description="Cancel the active run.",
                        input_schema={"type": "object", "properties": {}},
                        execute=request_cancel,
                    )
                ]
            )
        ),
        max_iterations=3,
        event_bus=bus,
    )

    result = await runtime.run("Please cancel.", session_id="session-cancel")

    assert result.status == LoopStatus.CANCELLED
    assert result.iterations == 1
    assert len(provider.requests) == 1
    event_types = [event.type for event in bus.history("session-cancel")]
    assert "run_start" in event_types
    assert "tool_call_start" in event_types
    assert "tool_result_appended" in event_types
    assert "run_cancelled" in event_types
    assert event_types[-1] == "run_finish"
    assert bus.history("session-cancel")[-1].payload["status"] == LoopStatus.CANCELLED


@pytest.mark.asyncio
async def test_agent_runtime_cancel_interrupts_foreground_shell_tool(tmp_path: Path):
    session_id = "session-shell-cancel"
    provider = ScriptedLLMProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_shell_cancel",
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps(
                                {
                                    "command": _python_command(
                                        "import sys, time; print('before'); sys.stdout.flush(); time.sleep(5)"
                                    ),
                                }
                            ),
                        },
                    }
                ]
            },
            {"content": "This should not be requested."},
        ]
    )
    runtime = AgentRuntime(
        provider=provider,
        config=RuntimeConfig(
            workspace_root=tmp_path,
            max_iterations=3,
            tool_permissions={"bash": "allow"},
        ),
    )

    async def cancel_later() -> None:
        await asyncio.sleep(0.1)
        runtime.cancel(session_id)

    cancel_task = asyncio.create_task(cancel_later())
    result = await asyncio.wait_for(
        runtime.run("Run a long shell command.", session_id=session_id),
        timeout=2,
    )
    await cancel_task

    assert result.status == LoopStatus.CANCELLED
    assert len(provider.requests) == 1
    history = runtime.store.read_history(session_id)
    tool_result = history[2].parts[0].tool_result
    assert tool_result is not None
    assert tool_result.status == "cancelled"
    assert tool_result.success is False
    assert tool_result.output["cancelled"] is True
    assert "before" in tool_result.content


def test_event_bus_run_state_import_boundary():
    code = """
import json
import sys

import efp_runtime.event_bus
import efp_runtime.runtime.run_state

print(json.dumps({"legacy_core_loaded": "src.agents.core" in sys.modules}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"legacy_core_loaded": False}

    combined = "\n".join(
        [
            (ROOT / "src/efp_runtime/event_bus.py").read_text(encoding="utf-8"),
            (ROOT / "src/efp_runtime/runtime/run_state.py").read_text(encoding="utf-8"),
        ]
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "from src.runtime",
        "import src.runtime",
        "from src.gateway",
        "import src.gateway",
        "from src.agents.core",
        "import src.agents.core",
    ]
    for token in forbidden_tokens:
        assert token not in combined
