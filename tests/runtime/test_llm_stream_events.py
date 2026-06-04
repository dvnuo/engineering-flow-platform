from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from efp_runtime.agents.profile import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.event_bus import RuntimeEventBus
from efp_runtime.llm.errors import ProviderContextOverflowError, ProviderTransientError
from efp_runtime.llm.events import LLMEvent, LLMEventType
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
from efp_runtime.models import Message
from efp_runtime.runtime import RuntimeConfig
from efp_runtime.session.models import MessagePartType
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class SequenceProvider:
    def __init__(self, steps):
        self.steps = list(steps)
        self.requests: list[RuntimeRequest] = []
        self.metadata_snapshots: list[dict] = []

    async def invoke(self, request: RuntimeRequest):
        self.requests.append(request)
        self.metadata_snapshots.append(deepcopy(request.provider_request.metadata))
        if not self.steps:
            raise AssertionError("SequenceProvider has no step left")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _runner(provider, **kwargs) -> RuntimeLoopRunner:
    return RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        **kwargs,
    )


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


def _llm_events(result):
    return [event for event in result.runtime_events if event.type.startswith("llm.")]


@pytest.mark.asyncio
async def test_non_streaming_response_emits_text_delta_and_step_finish_events():
    provider = SequenceProvider([{"content": "Done.", "usage": {"total_tokens": 5}}])
    runner = _runner(provider)

    result = await runner.run(session_id="session-llm-nonstream", user_text="Say done.")

    assert result.status == LoopStatus.COMPLETED
    text_events = _events(result, "llm.text_delta")
    assert len(text_events) == 1
    assert text_events[0].payload["run_id"] == result.runtime_events[0].payload["run_id"]
    assert text_events[0].payload["iteration"] == 1
    assert text_events[0].payload["llm_event_type"] == "text_delta"
    assert text_events[0].payload["delta"] == "Done."
    assert text_events[0].payload["text"] == "Done."

    finish_events = _events(result, "llm.step_finish")
    assert len(finish_events) == 1
    assert finish_events[0].payload["usage"] == {"total_tokens": 5}
    assert provider.requests[0].metadata["emit_llm_stream_events"] is True
    assert provider.requests[0].provider_request.metadata["emit_llm_stream_events"] is True


@pytest.mark.asyncio
async def test_async_generator_stream_events_preserve_final_assistant_message():
    async def stream():
        yield LLMEvent(LLMEventType.STEP_START)
        yield LLMEvent(LLMEventType.MESSAGE_START)
        yield LLMEvent(LLMEventType.TEXT_START, part_id="text_0")
        yield LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta="Hello")
        await asyncio.sleep(0)
        yield LLMEvent(LLMEventType.TEXT_DELTA, part_id="text_0", delta=" stream")
        yield LLMEvent(LLMEventType.TEXT_END, part_id="text_0")
        yield LLMEvent(LLMEventType.STEP_FINISH)

    provider = SequenceProvider([stream()])
    runner = _runner(provider)

    result = await runner.run(session_id="session-llm-async", user_text="Stream.")

    assert result.status == LoopStatus.COMPLETED
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.parts[0].type is MessagePartType.TEXT
    assert result.final_assistant_message.parts[0].text == "Hello stream"
    assert [event.payload["delta"] for event in _events(result, "llm.text_delta")] == [
        "Hello",
        " stream",
    ]


@pytest.mark.asyncio
async def test_tool_call_delta_and_done_events_include_call_and_arguments():
    provider = SequenceProvider(
        [
            {
                "tool_calls": [
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    }
                ]
            }
        ]
    )
    runner = _runner(provider, max_iterations=1)

    result = await runner.run(session_id="session-llm-tool", user_text="Read it.")

    assert result.status == LoopStatus.MAX_ITERATIONS
    delta_event = next(
        event
        for event in _events(result, "llm.tool_call_delta")
        if event.payload.get("arguments_delta")
    )
    assert delta_event.payload["tool_call_id"] == "call_read"
    assert delta_event.payload["tool_name"] == "read_file"
    assert delta_event.payload["arguments_delta"] == '{"path": "README.md"}'

    done_events = _events(result, "llm.tool_call_done")
    assert len(done_events) == 1
    assert done_events[0].payload["tool_call_id"] == "call_read"
    assert done_events[0].payload["tool_name"] == "read_file"
    assert done_events[0].payload["arguments"] == {"path": "README.md"}


@pytest.mark.asyncio
async def test_event_bus_receives_llm_events_between_iteration_boundaries():
    bus = RuntimeEventBus()
    subscription = bus.subscribe(session_id="session-llm-bus")
    provider = SequenceProvider([{"content": "Bus visible."}])
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        event_bus=bus,
    )

    result = await runner.run(session_id="session-llm-bus", user_text="Observe.")
    published = []
    while True:
        event = await asyncio.wait_for(subscription.get(), timeout=1)
        published.append(event)
        if event.type == "run_finish":
            break
    subscription.close()

    assert result.status == LoopStatus.COMPLETED
    event_types = [event.type for event in published]
    assert "llm.text_delta" in event_types
    assert event_types.index("iteration_start") < event_types.index("llm.text_delta")
    assert event_types.index("llm.text_delta") < event_types.index("iteration_finish")


@pytest.mark.asyncio
async def test_emit_llm_stream_events_false_suppresses_llm_runtime_events():
    provider = SequenceProvider([{"content": "Quiet."}])
    runner = _runner(provider, emit_llm_stream_events=False)

    result = await runner.run(session_id="session-llm-off", user_text="Quiet.")

    assert result.status == LoopStatus.COMPLETED
    assert _llm_events(result) == []
    assert [event.type for event in result.runtime_events] == [
        "run_start",
        "iteration_start",
        "iteration_finish",
        "run_finish",
    ]
    assert result.runtime_events[0].payload["emit_llm_stream_events"] is False
    assert provider.requests[0].metadata["emit_llm_stream_events"] is False


@pytest.mark.asyncio
async def test_provider_retry_only_emits_llm_events_for_successful_attempt():
    provider = SequenceProvider(
        [
            ProviderTransientError("temporary outage", code="rate_limit"),
            {"content": "Recovered."},
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-llm-retry", user_text="Retry.")

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.retry")) == 1
    assert [event.payload["text"] for event in _events(result, "llm.text_delta")] == [
        "Recovered."
    ]
    assert len(_events(result, "llm.step_start")) == 1


@pytest.mark.asyncio
async def test_context_overflow_retry_only_emits_llm_events_for_successful_attempt():
    provider = SequenceProvider(
        [
            ProviderContextOverflowError("context too long"),
            {"content": "Recovered after overflow."},
        ]
    )
    runner = _runner(provider, max_context_parts=2)
    session = Message.from_text("user", "old request", message_id="msg-old")

    result = await runner.run(
        session_id="session-llm-overflow",
        user_text="Retry with compacted context.",
        context_messages=[session],
    )

    assert result.status == LoopStatus.COMPLETED
    assert len(provider.requests) == 2
    assert len(_events(result, "provider.context_overflow_retry")) == 1
    assert [event.payload["text"] for event in _events(result, "llm.text_delta")] == [
        "Recovered after overflow."
    ]
    assert len(_events(result, "llm.step_start")) == 1


def test_subagent_child_config_preserves_emit_llm_stream_events():
    base_config = RuntimeConfig(
        max_iterations=2,
        emit_llm_stream_events=False,
        metadata={"suite": "stream-events"},
    )

    child = _child_config(
        profile=AgentProfile(name="general"),
        base_config=base_config,
        workspace_root=None,
        metadata={"task_id": "task-stream"},
    )

    assert child.emit_llm_stream_events is False
    assert child.metadata == {"suite": "stream-events", "task_id": "task-stream"}
    assert RuntimeConfig().emit_llm_stream_events is True


@pytest.mark.asyncio
async def test_bridge_projects_new_llm_event_contract_payloads():
    from efp_runtime.loop.stream_events import bridge_llm_stream_events

    runtime_events = []
    llm_events = [
        LLMEvent(
            LLMEventType.REASONING_START,
            part_id="reasoning-1",
            metadata={"item_id": "rs_1"},
            provider_metadata={"openai": {"itemId": "rs_1"}},
            raw={"type": "response.output_item.added", "item_id": "rs_1"},
        ),
        LLMEvent(
            LLMEventType.REASONING_DELTA,
            part_id="reasoning-1",
            delta="think",
        ),
        LLMEvent(LLMEventType.REASONING_END, part_id="reasoning-1"),
        LLMEvent(
            LLMEventType.FINISH,
            usage={"total_tokens": 7},
            metadata={"finish_reason": "stop"},
        ),
        LLMEvent(
            LLMEventType.PROVIDER_ERROR,
            error="rate_limit_exceeded: Slow down",
            metadata={"code": "rate_limit_exceeded", "retryable": True},
            provider_metadata={"openai": {"error": {"code": "rate_limit_exceeded"}}},
            raw={"type": "response.failed", "response": {"id": "resp_1"}},
        ),
        LLMEvent(
            LLMEventType.TOOL_ERROR,
            tool_call_id="call-1",
            tool_name="search",
            error="tool failed",
        ),
    ]

    yielded = [
        event
        async for event in bridge_llm_stream_events(
            llm_events,
            runtime_events=runtime_events,
            session_id="s-1",
            run_id="run-1",
            iteration=1,
        )
    ]

    assert yielded == llm_events
    assert [event.type for event in runtime_events] == [
        "llm.reasoning_start",
        "llm.reasoning_delta",
        "llm.reasoning_end",
        "llm.finish",
        "llm.provider_error",
        "llm.tool_error",
    ]
    reasoning_payload = runtime_events[0].payload
    assert reasoning_payload["event_type"] == "reasoning_start"
    assert reasoning_payload["metadata"] == {"item_id": "rs_1"}
    assert reasoning_payload["provider_metadata"] == {"openai": {"itemId": "rs_1"}}
    assert reasoning_payload["raw"] == {
        "type": "response.output_item.added",
        "item_id": "rs_1",
    }
    assert runtime_events[3].payload["usage"] == {"total_tokens": 7}
    assert runtime_events[4].payload["code"] == "rate_limit_exceeded"
    assert runtime_events[4].payload["retryable"] is True
    assert runtime_events[5].payload["tool_call_id"] == "call-1"


def test_llm_stream_events_import_boundary():
    code = """
import json
import sys

import efp_runtime.loop.stream_events

blocked = [
    "src.sessions",
    "src.agents.core",
    "src.runtime",
    "src.skills",
]
print(json.dumps([name for name in blocked if name in sys.modules]))
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
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
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
