"""The loop's cost controls: credits guard, step cap, request-path pruning, sticky compaction, calibration."""

from __future__ import annotations

import pytest

from efp_runtime.compaction.strategy import (
    BudgetCompactionStrategy,
    ContextBudget,
    StickyBudgetCompactionStrategy,
)
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.session.models import Message, MessagePart, Session
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.definition import ToolDef
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall, ToolResult


def _tool_runtime(name: str = "again", output: str = "ok") -> ToolRuntime:
    async def execute(args, context):
        return output

    return ToolRuntime(
        ToolRegistry(
            [
                ToolDef(
                    id=name,
                    description="Return ok",
                    input_schema={"type": "object", "properties": {}},
                    execute=execute,
                )
            ]
        )
    )


def _tool_call_response(name: str = "again", call_id: str = "call_1", usage=None) -> dict:
    response = {
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ]
    }
    if usage:
        response["usage"] = usage
    return response


def _events(result, event_type: str):
    return [event for event in result.runtime_events if event.type == event_type]


def _tool_result_contents(request) -> list[str]:
    contents: list[str] = []
    for message in request.provider_request.messages:
        for part in message.parts:
            if part.tool_result is not None:
                contents.append(part.tool_result.content or "")
    return contents


def _runner(store, provider, **kwargs) -> RuntimeLoopRunner:
    kwargs.setdefault("tool_runtime", ToolRuntime(ToolRegistry()))
    return RuntimeLoopRunner(store=store, provider=provider, **kwargs)


# --- step cap and credits guard ------------------------------------------------


@pytest.mark.asyncio
async def test_step_cap_keeps_tools_in_the_prompt_and_pauses_the_budget():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "Summary."}])
    runner = _runner(store, provider, tool_runtime=_tool_runtime(), max_iterations=1)

    result = await runner.run(session_id="s-cap", user_text="Go.")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert [tool.name for tool in request.provider_request.tools] == ["again"]
    assert request.metadata["tool_choice"] == "none"
    assert "CRITICAL - MAXIMUM STEPS REACHED" in request.provider_request.messages[-1].text
    paused = _events(result, "run_budget_paused")
    assert len(paused) == 1
    assert paused[0].payload["reason"] == "iteration_cap"
    assert paused[0].payload["iteration_limit"] == 1
    assert _events(result, "loop.max_iterations")[0].payload["tools_disabled"] is False
    assert _events(result, "run_finish")[0].payload["terminal_reason"] == "max_steps_reached"


@pytest.mark.asyncio
async def test_operator_can_still_drop_the_tools_on_the_final_step():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "Summary."}])
    runner = _runner(
        store,
        provider,
        tool_runtime=_tool_runtime(),
        max_iterations=1,
        keep_tools_on_final_iteration=False,
    )

    await runner.run(session_id="s-cap-drop", user_text="Go.")

    request = provider.requests[0]
    assert request.provider_request.tools == []
    assert "tool_choice" not in request.metadata


@pytest.mark.asyncio
async def test_credits_guard_turns_the_next_iteration_into_a_summary():
    store = InMemorySessionStore()
    # 5M input tokens on gpt-6-sol: the long-context tier, about 2000 credits.
    expensive = {"input_tokens": 5_000_000, "output_tokens": 1_000}
    provider = ScriptedLLMProvider(
        [
            _tool_call_response(usage=expensive),
            {"content": "Budget summary.", "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]
    )
    runner = _runner(
        store,
        provider,
        tool_runtime=_tool_runtime(),
        default_model="gpt-6-sol",
        run_credits_limit=100,
    )

    result = await runner.run(session_id="s-credits", user_text="Go.")

    assert result.status == LoopStatus.COMPLETED
    assert result.iterations == 2
    second = provider.requests[1]
    assert second.metadata["tool_choice"] == "none"
    assert second.metadata["credits_guard_reached"] is True
    assert second.metadata["loop"]["credits_guard_limit"] == 100.0
    assert "CREDITS BUDGET REACHED" in second.provider_request.messages[-1].text
    assert [tool.name for tool in second.provider_request.tools] == ["again"]

    reached = _events(result, "run_credits_limit_reached")
    assert len(reached) == 1
    assert reached[0].payload["estimated_credits"] >= 100
    paused = _events(result, "run_budget_paused")
    assert len(paused) == 1
    assert paused[0].payload["reason"] == "credits_guard"
    assert paused[0].payload["credits_limit"] == 100.0
    assert _events(result, "run_finish")[0].payload["terminal_reason"] == "budget_paused"

    usage = result.usage
    assert usage["estimated_credits"] == pytest.approx(paused[0].payload["estimated_credits"])
    assert usage["iterations"] == 2
    assert usage["pricing"]["model"] == "gpt-6-sol"
    assert usage["pricing"]["tier"] == "long_context"
    assert usage["pricing"]["tiers_seen"] == ["default", "long_context"]
    first_step = _events(result, "usage.updated")[0].payload
    assert first_step["pricing"]["tier"] == "long_context"
    assert first_step["step_usage"]["estimated_credits"] == pytest.approx(2001.5)

    assert result.final_assistant_message is not None
    stamped = result.final_assistant_message.metadata["run_usage"]
    assert stamped["estimated_credits"] == usage["estimated_credits"]
    assert store.read_history(result.session_id)[-1].metadata["run_usage"]["iterations"] == 2


@pytest.mark.asyncio
async def test_usage_carries_credits_cache_ratio_and_pricing():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider(
        [
            {
                "content": "ok",
                "usage": {
                    "input_tokens": 100_000,
                    "output_tokens": 1_000,
                    "input_tokens_details": {"cached_tokens": 50_000},
                },
            }
        ]
    )
    runner = _runner(store, provider, default_model="gpt-6-luna")

    result = await runner.run(session_id="s-usage", user_text="Go.")

    expected_usd = (50_000 * 0.10 + 50_000 * 0.01 + 1_000 * 0.50) / 1e6
    assert result.usage["estimated_credits"] == pytest.approx(expected_usd * 100, rel=1e-3)
    assert result.usage["cache_hit_ratio"] == pytest.approx(0.5)
    assert result.usage["iterations"] == 1
    assert result.usage["pricing"]["tier"] == "default"
    assert result.usage["pricing"]["threshold_tokens"] == 272_000
    event = _events(result, "usage.updated")[0].payload
    assert event["estimated_credits"] == pytest.approx(result.usage["estimated_credits"])
    assert event["pricing"]["model"] == "gpt-6-luna"


@pytest.mark.asyncio
async def test_credits_are_not_estimated_off_copilot():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([{"content": "ok", "usage": {"input_tokens": 100, "output_tokens": 1}}])
    runner = _runner(store, provider, default_provider_id="ai_platform", default_model="gpt-5.6-sol")

    result = await runner.run(session_id="s-ai", user_text="Go.")

    assert result.usage["estimated_credits"] is None
    assert "pricing" not in result.usage
    assert _events(result, "usage.updated")[0].payload["pricing"] is None


# --- request-path pruning -----------------------------------------------------


def _tool_pair(call_id: str, content: str) -> list[Message]:
    call = ToolCall(call_id=call_id, tool_name="search", arguments={})
    result = ToolResult(
        call_id=call_id,
        tool_name="search",
        content=content,
        success=True,
        status="success",
    )
    return [
        Message(
            role="assistant",
            message_id=f"{call_id}-call",
            parts=[MessagePart.tool_call_part(call)],
        ),
        Message(
            role="tool",
            message_id=f"{call_id}-result",
            parts=[MessagePart.tool_result_part(result)],
        ),
    ]


def _three_turn_session(session_id: str, payload: str) -> Session:
    messages: list[Message] = []
    for index in (1, 2, 3):
        messages.append(Message.from_text("user", f"turn {index}", message_id=f"u{index}"))
        messages.extend(_tool_pair(f"c{index}", payload))
        messages.append(Message.from_text("assistant", f"done {index}", message_id=f"a{index}"))
    return Session(session_id=session_id, messages=messages)


@pytest.mark.asyncio
async def test_request_path_prunes_old_tool_outputs_but_keeps_the_transcript():
    store = InMemorySessionStore()
    big = "x" * 30_000
    session = _three_turn_session("s-prune", big)
    provider = ScriptedLLMProvider([_tool_call_response(name="lookup", call_id="c4"), {"content": "ok"}])
    runner = _runner(
        store,
        provider,
        tool_runtime=_tool_runtime(name="lookup", output="fresh"),
        compaction_prune=True,
        compaction_prune_protect_chars=1_000,
        compaction_prune_min_chars=100,
        compaction_tool_output_max_chars=50,
    )

    result = await runner.run(session=session, user_text="turn 4")

    assert result.status == LoopStatus.COMPLETED
    first_request = provider.requests[0]
    contents = _tool_result_contents(first_request)
    assert len(contents) == 3
    # Turns 3 and 4 are protected; the two older results shrink to a marker.
    assert contents[0].startswith("x" * 50)
    assert "[Old tool result content cleared for context compaction" in contents[0]
    assert "[Old tool result content cleared for context compaction" in contents[1]
    assert contents[2] == big
    assert first_request.metadata["request_prune"]["pruned_result_count"] == 2
    assert first_request.metadata["request_prune"]["pruned_chars"] > 50_000

    pruned_events = _events(result, "request_tool_outputs_pruned")
    assert len(pruned_events) == 1
    assert pruned_events[0].payload["pruned_result_count"] == 2
    usage_snapshot = _events(result, "context_usage")[0].payload
    assert usage_snapshot["pruned_tool_outputs"]["count"] == 2

    # The second iteration reuses the decision: same prefix, the new result intact.
    second_request = provider.requests[1]
    second_contents = _tool_result_contents(second_request)
    assert second_contents[:3] == contents
    assert second_contents[3] == "fresh"

    stored = store.read_history(result.session_id)
    stored_results = [
        part.tool_result.content
        for message in stored
        for part in message.parts
        if part.tool_result is not None
    ]
    assert stored_results[0] == big
    assert stored_results[1] == big


@pytest.mark.asyncio
async def test_request_path_pruning_is_off_by_default_for_the_bare_runner():
    store = InMemorySessionStore()
    big = "x" * 30_000
    provider = ScriptedLLMProvider([{"content": "ok"}])
    runner = _runner(store, provider)

    await runner.run(session=_three_turn_session("s-noprune", big), user_text="turn 4")

    assert all(content == big for content in _tool_result_contents(provider.requests[0]))
    assert "request_prune" not in provider.requests[0].metadata


# --- sticky compaction ----------------------------------------------------------


def _messages(count: int, *, start: int = 0, size: int = 100) -> list[Message]:
    messages = []
    for index in range(start, start + count):
        role = "user" if index % 2 == 0 else "assistant"
        text = f"m{index} " + "x" * (size - len(f"m{index} "))
        messages.append(Message.from_text(role, text, message_id=f"m{index}"))
    return messages


def test_sticky_strategy_cuts_to_the_target_then_holds_the_cut():
    # 20 messages of 2k chars against a 12k budget: the compaction summary
    # (a few hundred chars) is small next to the messages, as in real sessions.
    messages = _messages(20, size=2_000)
    budget = ContextBudget(max_chars=12_000, reserve_chars=0)

    first = StickyBudgetCompactionStrategy(budget=budget, target_ratio=0.5).compact(messages)
    assert first.compacted
    assert first.boundary_message_id is not None
    assert first.kept_messages[0].message_id == first.boundary_message_id
    assert sum(len(message.parts[0].text or "") for message in first.kept_messages) <= 6_000

    more = messages + _messages(1, start=20, size=2_000)
    second = StickyBudgetCompactionStrategy(
        budget=budget,
        boundary_message_id=first.boundary_message_id,
        target_ratio=0.5,
    ).compact(more)
    assert second.boundary_message_id == first.boundary_message_id
    assert [m.message_id for m in second.compacted_messages] == [
        m.message_id for m in first.compacted_messages
    ]
    assert (
        second.messages[0].parts[0].compaction.summary
        == first.messages[0].parts[0].compaction.summary
    )

    # The plain strategy keeps whatever newest blocks fit, so its cut moves.
    plain = BudgetCompactionStrategy(budget=budget).compact(more)
    assert [m.message_id for m in plain.compacted_messages] != [
        m.message_id for m in first.compacted_messages
    ]

    # Once the kept part outgrows the budget the cut moves again, to a new target.
    many = more + _messages(5, start=21, size=2_000)
    third = StickyBudgetCompactionStrategy(
        budget=budget,
        boundary_message_id=first.boundary_message_id,
        target_ratio=0.5,
    ).compact(many)
    assert third.compacted
    assert third.boundary_message_id != first.boundary_message_id


def test_sticky_strategy_without_a_cut_leaves_a_small_history_alone():
    messages = _messages(3)
    result = StickyBudgetCompactionStrategy(
        budget=ContextBudget(max_chars=10_000, reserve_chars=0),
    ).compact(messages)
    assert not result.compacted
    assert result.boundary_message_id is None
    with pytest.raises(ValueError):
        StickyBudgetCompactionStrategy(budget=ContextBudget(max_chars=10), target_ratio=0)


@pytest.mark.asyncio
async def test_sticky_compaction_keeps_the_cut_between_iterations_and_persists_it():
    store = InMemorySessionStore()
    session = Session(session_id="s-sticky", messages=_messages(10, size=400))
    provider = ScriptedLLMProvider([_tool_call_response(), {"content": "done"}])
    runner = _runner(
        store,
        provider,
        tool_runtime=_tool_runtime(),
        max_context_chars=2_000,
        context_reserve_chars=0,
        compaction_sticky_boundary=True,
        compaction_target_ratio=0.5,
    )

    result = await runner.run(session=session, user_text="go")

    assert result.status == LoopStatus.COMPLETED
    first, second = provider.requests
    first_boundary = first.prepared_request.compaction_metadata["boundary_message_id"]
    second_boundary = second.prepared_request.compaction_metadata["boundary_message_id"]
    assert first_boundary and first_boundary == second_boundary
    first_texts = [message.text for message in first.provider_request.messages]
    second_texts = [message.text for message in second.provider_request.messages]
    assert second_texts[: len(first_texts)] == first_texts
    assert store.get_session("s-sticky").metadata["compaction_boundary_message_id"] == first_boundary


# --- budget by pricing tier and calibration -------------------------------------


def test_default_budget_stops_at_the_default_pricing_tier():
    store = InMemorySessionStore()
    provider = ScriptedLLMProvider([])
    tier_window_chars = (272_000 + 128_000 - 8_000) * 4

    runner = _runner(store, provider, default_model="gpt-6.1-sol")
    budget = runner._context_budget()
    assert budget.max_chars == tier_window_chars
    assert budget.reserve_chars == 128_000 * 4
    metadata = runner._model_context_budget_metadata()
    assert metadata["billing_default_prompt_cap_tokens"] == 272_000
    assert metadata["long_context_budget"] is False
    assert metadata["max_context_tokens"] == 272_000 + 128_000 - 8_000

    long_runner = _runner(store, provider, default_model="gpt-6.1-sol", copilot_long_context=True)
    assert long_runner._context_budget().max_chars == (1_050_000 - 8_000) * 4

    ai_runner = _runner(store, provider, default_provider_id="ai_platform", default_model="gpt-5.6-sol")
    assert ai_runner._context_budget().max_chars == (1_000_000 - 8_000) * 4

    explicit = _runner(store, provider, default_model="gpt-6.1-sol", max_context_tokens=1_000_000)
    assert explicit._context_budget().max_chars == 1_000_000 * 4


@pytest.mark.asyncio
async def test_token_calibration_is_learned_from_real_input_tokens_and_persisted():
    store = InMemorySessionStore()
    # 12k ASCII chars estimate to about 3k tokens; the provider reports 9k.
    provider = ScriptedLLMProvider(
        [{"content": "ok", "usage": {"input_tokens": 9_000, "output_tokens": 5}}]
    )
    runner = _runner(store, provider, default_model="gpt-6.1-sol")

    result = await runner.run(session_id="s-cal", user_text="y" * 12_000)

    assert result.status == LoopStatus.COMPLETED
    calibration = store.get_session("s-cal").metadata["context_token_calibration"]
    assert 2.5 < calibration["ratio"] < 3.5
    assert calibration["samples"] == 1
    assert calibration["last_input_tokens"] == 9_000

    provider_two = ScriptedLLMProvider([{"content": "ok"}])
    runner_two = _runner(store, provider_two, default_model="gpt-6.1-sol")
    result_two = await runner_two.run(session_id="s-cal", user_text="again")

    request = provider_two.requests[0]
    uncalibrated = (272_000 + 128_000 - 8_000) * 4
    assert request.provider_request.metadata["token_calibration_ratio"] > 2
    assert request.provider_request.metadata["context_budget"]["max_chars"] < uncalibrated / 2
    snapshot = _events(result_two, "context_usage")[0].payload
    assert snapshot["calibration"]["ratio"] > 2
    assert snapshot["calibrated_used_tokens"] > snapshot["used_tokens"]
    assert snapshot["billing_threshold_tokens"] == 272_000
    assert snapshot["billing_tier"] == "default"
    assert snapshot["budget_tokens"] > 0
