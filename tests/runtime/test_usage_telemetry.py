from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from efp_runtime.agents.profile import AgentProfile
from efp_runtime.agents.task_runner import _child_config
from efp_runtime.llm.events import LLMEvent, LLMEventType
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.runtime import RuntimeConfig
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.usage import normalize_usage


ROOT = Path(__file__).resolve().parents[2]


def _runner(provider, **kwargs) -> RuntimeLoopRunner:
    return RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        **kwargs,
    )


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


@pytest.mark.asyncio
async def test_non_streaming_prompt_completion_usage_aggregates_to_result():
    provider = ScriptedLLMProvider(
        [
            {
                "content": "Done.",
                "usage": {"prompt_tokens": 9, "completion_tokens": 4},
            }
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-usage-nonstream", user_text="Go.")

    assert result.status == LoopStatus.COMPLETED
    assert result.usage["input_tokens"] == 9
    assert result.usage["output_tokens"] == 4
    assert result.usage["total_tokens"] == 13
    assert result.usage["cost_usd"] is None
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.usage == {
        "prompt_tokens": 9,
        "completion_tokens": 4,
    }

    request = provider.requests[0]
    assert request.metadata["track_usage"] is True
    assert request.metadata["usage_pricing_enabled"] is False
    assert request.provider_request.metadata["usage_telemetry"] == {
        "track_usage": True,
        "pricing_enabled": False,
    }

    usage_events = _events(result, "usage.updated")
    assert len(usage_events) == 1
    assert usage_events[0].payload["step_usage"]["input_tokens"] == 9
    assert usage_events[0].payload["usage"]["total_tokens"] == 13
    assert _events(result, "iteration_finish")[0].payload["run_usage"]["total_tokens"] == 13
    assert _events(result, "run_finish")[0].payload["usage"]["total_tokens"] == 13


@pytest.mark.asyncio
async def test_async_stream_step_finish_usage_accumulates():
    async def stream():
        yield LLMEvent(LLMEventType.STEP_START)
        yield LLMEvent(LLMEventType.MESSAGE_START)
        yield LLMEvent(LLMEventType.TEXT_DELTA, delta="First")
        yield LLMEvent(
            LLMEventType.STEP_FINISH,
            usage={"prompt_tokens": 2, "completion_tokens": 3},
        )
        yield LLMEvent(
            LLMEventType.STEP_FINISH,
            usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
        )

    provider = ScriptedLLMProvider([stream()])
    runner = _runner(provider)

    result = await runner.run(session_id="session-usage-stream", user_text="Stream.")

    assert result.status == LoopStatus.COMPLETED
    assert result.usage["input_tokens"] == 7
    assert result.usage["output_tokens"] == 10
    assert result.usage["total_tokens"] == 17
    assert [event.payload["usage"]["total_tokens"] for event in _events(result, "usage.updated")] == [
        5,
        17,
    ]


@pytest.mark.asyncio
async def test_reasoning_and_cached_usage_fields_are_standardized():
    provider = ScriptedLLMProvider(
        [
            {
                "content": "Reasoned.",
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "prompt_tokens_details": {"cached_tokens": 25},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                    "total_tokens": 140,
                },
            }
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-usage-details", user_text="Think.")

    assert result.usage["input_tokens"] == 100
    assert result.usage["output_tokens"] == 40
    assert result.usage["reasoning_tokens"] == 10
    assert result.usage["cached_input_tokens"] == 25
    assert result.usage["total_tokens"] == 140

    summary = normalize_usage(
        {
            "input_tokens": 8,
            "output_tokens": 3,
            "reasoning_output_tokens": 2,
            "cached_input_tokens": 4,
        }
    )
    assert summary.input_tokens == 8
    assert summary.output_tokens == 3
    assert summary.reasoning_tokens == 2
    assert summary.cached_input_tokens == 4
    assert summary.total_tokens == 13


@pytest.mark.asyncio
async def test_usage_pricing_estimates_cost_usd():
    pricing = {
        "input_per_1m": 1.0,
        "output_per_1m": 2.0,
        "reasoning_per_1m": 4.0,
        "cached_input_per_1m": 0.5,
    }
    provider = ScriptedLLMProvider(
        [
            {
                "content": "Priced.",
                "usage": {
                    "input_tokens": 1_000_000,
                    "output_tokens": 2_000_000,
                    "reasoning_tokens": 500_000,
                    "cached_input_tokens": 250_000,
                    "total_tokens": 3_500_000,
                },
            }
        ]
    )
    runner = _runner(provider, usage_pricing=pricing)

    result = await runner.run(session_id="session-usage-priced", user_text="Price.")

    assert result.usage["cost_usd"] == pytest.approx(7.125)
    assert _events(result, "usage.updated")[0].payload["pricing_enabled"] is True
    assert provider.requests[0].metadata["usage_pricing_enabled"] is True


@pytest.mark.asyncio
async def test_track_usage_false_keeps_message_usage_without_run_summary():
    provider = ScriptedLLMProvider(
        [
            {
                "content": "Untracked.",
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }
        ]
    )
    runner = _runner(provider, track_usage=False)

    result = await runner.run(session_id="session-usage-off", user_text="No summary.")

    assert result.status == LoopStatus.COMPLETED
    assert result.usage == {}
    assert result.final_assistant_message is not None
    assert result.final_assistant_message.usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
    assert _events(result, "usage.updated") == []
    assert "usage" not in _events(result, "run_finish")[0].payload
    assert provider.requests[0].metadata["track_usage"] is False


def test_subagent_child_config_preserves_usage_telemetry_config():
    base_config = RuntimeConfig(
        max_iterations=2,
        track_usage=False,
        usage_pricing={"input_per_1m": 1.25},
        metadata={"suite": "usage"},
    )

    child = _child_config(
        profile=AgentProfile(name="general"),
        base_config=base_config,
        workspace_root=None,
        metadata={"task_id": "task-usage"},
    )

    assert child.track_usage is False
    assert child.usage_pricing == {"input_per_1m": 1.25}
    assert child.metadata == {"suite": "usage", "task_id": "task-usage"}


def test_usage_pricing_rejects_negative_values():
    with pytest.raises(ValueError, match="usage_pricing"):
        RuntimeConfig(usage_pricing={"input_per_1m": -0.01})


def test_usage_telemetry_import_boundary():
    code = """
import json
import sys

import efp_runtime.usage

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

    source = (ROOT / "src/efp_runtime/usage.py").read_text(encoding="utf-8")
    assert "from src.efp_runtime" not in source
    assert "import src.efp_runtime" not in source
