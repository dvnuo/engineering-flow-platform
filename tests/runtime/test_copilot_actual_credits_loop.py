"""Copilot's own charge (``usage.copilot_usage.total_nano_aiu``) replaces the credits estimate."""

from __future__ import annotations

import pytest

from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


def _runner(provider, **kwargs) -> RuntimeLoopRunner:
    kwargs.setdefault("tool_runtime", ToolRuntime(ToolRegistry()))
    kwargs.setdefault("default_provider_id", "github-copilot")
    return RuntimeLoopRunner(store=InMemorySessionStore(), provider=provider, **kwargs)


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


@pytest.mark.asyncio
async def test_the_gateway_charge_is_the_credits_figure_of_the_run():
    provider = ScriptedLLMProvider(
        [
            {
                "content": "Done.",
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 80,
                    "copilot_usage": {"total_nano_aiu": 1_250_000_000},
                },
            }
        ]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-charge", user_text="Go.")

    assert result.status == LoopStatus.COMPLETED
    assert result.usage["actual_credits"] == 1.25
    assert result.usage["estimated_credits"] == 1.25
    assert result.usage["credits_source"] == "copilot_usage"
    assert result.usage["pricing"]["source"] == "copilot_usage"
    assert result.usage["pricing"]["unit"] == "credits"
    updated = _events(result, "usage.updated")
    assert updated
    assert updated[-1].payload["estimated_credits"] == 1.25
    assert updated[-1].payload["pricing"]["source"] == "copilot_usage"
    assert updated[-1].payload["step_usage"]["actual_credits"] == 1.25


@pytest.mark.asyncio
async def test_without_a_gateway_charge_the_run_keeps_the_estimate_path():
    provider = ScriptedLLMProvider(
        [{"content": "Done.", "usage": {"input_tokens": 1200, "output_tokens": 80}}]
    )
    runner = _runner(provider)

    result = await runner.run(session_id="session-estimate", user_text="Go.")

    assert result.usage["actual_credits"] is None
    # No billing model is known to a scripted provider, so there is no
    # estimate either; the source says so instead of pretending.
    assert result.usage["credits_source"] in (None, "estimate")
    assert "pricing" not in result.usage or result.usage["pricing"].get("source") != "copilot_usage"
