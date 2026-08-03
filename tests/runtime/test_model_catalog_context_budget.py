"""Regression tests for the model-catalog-derived default context budget.

Before this behavior existed an unconfigured native runtime had
``max_context_chars``/``max_context_tokens`` unset, so ``ContextBudget.max_chars``
was ``None``, no compaction strategy was built at render time, and the full
history went to the provider - which is how a long gpt-5.6-sol chat produced
``400 Bad Request / "Your input exceeds the context window of this model."``.

Every test here fails on that behavior.
"""

from __future__ import annotations

import pytest

from efp_runtime.compaction import summary as summary_module
from efp_runtime.compaction.strategy import BudgetCompactionStrategy, ContextBudget
from efp_runtime.llm.models import (
    context_safety_margin_tokens,
    default_max_context_tokens,
    resolve_model_context_profile,
)
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, ScriptedLLMProvider
from efp_runtime.models import Message, MessagePart, MessagePartType, MessageRole
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime


SOL_WINDOW_TOKENS = 400_000
SOL_SAFETY_TOKENS = 8_000
SOL_MAX_CHARS = (SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS) * 4  # 1_568_000
SOL_RESERVE_CHARS = 128_000 * 4  # 512_000


def _runner(store, provider, **kwargs) -> RuntimeLoopRunner:
    kwargs.setdefault("default_model", "gpt-5.6-sol")
    return RuntimeLoopRunner(
        store=store,
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
        **kwargs,
    )


def _seed_large_history(store, session_id: str, *, messages: int, chars: int) -> None:
    store.create_session(session_id=session_id)
    per_message = chars // messages
    for index in range(messages):
        store.append_message(
            session_id,
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            parts=[MessagePart.text_part("word " * (per_message // 5))],
            message_id=f"msg-bulk-{index}",
            status="complete",
        )


def _request_chars(provider_request) -> int:
    return sum(len(message.text or "") for message in provider_request.messages)


# --------------------------------------------------------------------------
# The budget itself
# --------------------------------------------------------------------------


def test_unconfigured_runtime_derives_char_budget_from_model_catalog():
    runner = _runner(InMemorySessionStore(), ScriptedLLMProvider([]))

    budget = runner._context_budget()

    assert budget.max_chars == SOL_MAX_CHARS
    assert budget.reserve_chars == SOL_RESERVE_CHARS
    assert budget.effective_max_chars == SOL_MAX_CHARS - SOL_RESERVE_CHARS
    # The whole point: a stock config must not leave the request unbounded.
    assert budget.max_chars is not None


def test_default_budget_leaves_room_for_the_declared_response_reserve():
    profile = resolve_model_context_profile("gpt-5.6-sol")

    prompt_tokens = default_max_context_tokens(profile) - profile.default_reserve_tokens

    assert context_safety_margin_tokens(profile) == SOL_SAFETY_TOKENS
    assert (
        prompt_tokens + profile.default_reserve_tokens + SOL_SAFETY_TOKENS
        == profile.context_window_tokens
    )


@pytest.mark.parametrize(
    ("model", "expected_window"),
    [
        ("gpt-5.6-sol", 400_000),
        ("gpt-5.6-terra", 400_000),
        ("gpt-5.6-luna", 328_000),
        ("gpt-5.4", 400_000),
    ],
)
def test_catalog_models_all_produce_a_budget(model, expected_window):
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_model=model,
    )

    budget = runner._context_budget()
    margin = min(8_000, expected_window // 20)

    assert budget.max_chars == (expected_window - margin) * 4
    assert budget.effective_max_chars > 0


def test_catalog_lookup_is_by_model_id_not_provider_id():
    """ai_platform serves gpt-5.4 with the same 400k window as Copilot.

    Resolving by provider id sent those runtimes to the 64k conservative
    profile, which now binds as a real budget and would compact them roughly
    seven times too early.
    """

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_provider_id="ai_platform",
        default_model="gpt-5.4",
    )

    assert runner._context_budget().max_chars == SOL_MAX_CHARS


def test_unknown_model_still_falls_back_to_the_conservative_profile():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_model="some-unlisted-model",
    )

    budget = runner._context_budget()

    assert budget.max_chars == (64_000 - 3_200) * 4
    assert budget.effective_max_chars > 0


# --------------------------------------------------------------------------
# Explicit operator config keeps overriding
# --------------------------------------------------------------------------


def test_explicit_max_context_chars_overrides_the_catalog():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=100_000,
    )

    budget = runner._context_budget()

    assert budget.max_chars == 100_000
    assert budget.reserve_chars == 0


def test_explicit_max_context_tokens_overrides_the_catalog():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=50_000,
    )

    assert runner._context_budget().max_chars == 200_000


def test_budget_metadata_reports_the_catalog_as_the_source():
    runner = _runner(InMemorySessionStore(), ScriptedLLMProvider([]))

    metadata = runner._model_context_budget_metadata()

    assert metadata["context_budget"]["max_context_chars_source"] == (
        "profile_context_window_tokens"
    )
    assert metadata["context_safety_margin_tokens"] == SOL_SAFETY_TOKENS
    assert metadata["max_context_tokens"] == SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS


def test_explicit_config_metadata_still_names_the_explicit_source():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=50_000,
    )

    metadata = runner._model_context_budget_metadata()

    assert metadata["context_budget"]["max_context_chars_source"] == "max_context_tokens"
    assert metadata["context_safety_margin_tokens"] is None


# --------------------------------------------------------------------------
# The reserve may never swallow the budget
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"context_reserve_tokens": 400_000},
        {"compaction_reserved_chars": 2_000_000},
        {"context_reserve_chars": 4_000_000},
    ],
)
def test_oversized_reserve_cannot_zero_the_prompt_budget(kwargs):
    """Reserve knobs are portal-managed and were inert while max_chars was None.

    Unclamped they drive effective_max_chars to 0, which compacts every request
    down to system context plus the latest turn with no event and no error.
    """

    runner = _runner(InMemorySessionStore(), ScriptedLLMProvider([]), **kwargs)

    budget = runner._context_budget()

    assert budget.reserve_chars == SOL_MAX_CHARS // 2
    assert budget.effective_max_chars == SOL_MAX_CHARS - SOL_MAX_CHARS // 2


def test_small_explicit_budget_is_not_zeroed_by_the_catalog_reserve():
    """max_context_tokens=50_000 gives 200_000 chars against a 512_000 reserve."""

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=50_000,
    )

    assert runner._context_budget().effective_max_chars == 100_000


def test_reasonable_reserve_is_left_alone():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        context_reserve_chars=1_000,
    )

    assert runner._context_budget().reserve_chars == 1_000


def test_overflow_retry_halves_the_effective_budget():
    runner = _runner(InMemorySessionStore(), ScriptedLLMProvider([]))
    budget = runner._context_budget()

    retry = runner._overflow_retry_budget()

    assert retry.reserve_chars == budget.reserve_chars
    assert retry.effective_max_chars == budget.effective_max_chars // 2
    assert retry.effective_max_chars > 0


# --------------------------------------------------------------------------
# End-to-end: request bounded, stored history untouched
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_long_session_request_is_bounded_by_the_default_budget():
    store = InMemorySessionStore()
    _seed_large_history(store, "session-big", messages=40, chars=1_800_000)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-big", user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.prepared_request.compaction_applied is True
    assert _request_chars(request.provider_request) < 1_800_000
    assert (
        request.prepared_request.compaction_metadata["kept_chars"]
        <= runner._context_budget().effective_max_chars
    )


@pytest.mark.asyncio
async def test_default_budget_never_rewrites_stored_history():
    """The catalog default sizes the in-memory request only.

    Stored history is the Portal transcript; rewriting it on a default that no
    operator opted into would destroy the user's conversation on disk.
    """

    store = InMemorySessionStore()
    _seed_large_history(store, "session-keep", messages=40, chars=1_800_000)
    before = len(store.read_history("session-keep"))
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-keep", user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    after = store.read_history("session-keep")
    # Only the new user turn and the new assistant turn were appended.
    assert len(after) == before + 2
    assert not [
        part
        for message in after
        for part in message.parts
        if part.type is MessagePartType.COMPACTION
    ]


@pytest.mark.asyncio
async def test_request_compaction_is_reported_as_a_runtime_event():
    store = InMemorySessionStore()
    _seed_large_history(store, "session-event", messages=40, chars=1_800_000)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-event", user_text="latest request")

    events = [
        event for event in result.runtime_events if event.type == "session_compacted"
    ]
    assert len(events) == 1
    assert events[0].payload["stored"] is False
    assert events[0].payload["scope"] == "request"
    assert events[0].payload["compacted_message_count"] > 0


@pytest.mark.asyncio
async def test_short_session_is_not_compacted_by_the_default_budget():
    store = InMemorySessionStore()
    _seed_large_history(store, "session-small", messages=4, chars=400)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-small", user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    assert provider.requests[0].prepared_request.compaction_applied is False
    assert not [
        event for event in result.runtime_events if event.type == "session_compacted"
    ]


# --------------------------------------------------------------------------
# The budget search must not block the event loop
# --------------------------------------------------------------------------


def test_budget_search_scans_each_message_once(monkeypatch):
    """Guards the quadratic re-scan that made this default unusable.

    The budget search re-derives the compaction summary for every candidate
    selection, and rendering that summary scans every compacted message's text
    with the relevant-file regex. Without a per-call memo that is O(blocks x
    history chars): a 3 MB / 600 block history took ~170 s of synchronous,
    event-loop-blocking CPU per LLM iteration - on the exact long conversations
    this budget exists to bound.

    Asserted structurally rather than by wall clock so the guard is
    machine-independent and fails fast.
    """

    message_count = 600
    # One scan per message, plus a small constant for the final summary build.
    # The quadratic implementation made ~message_count scans per candidate.
    scan_limit = 4 * message_count
    calls = {"count": 0}
    real_pattern = summary_module._PATH_PATTERN

    class _CountingPattern:
        def findall(self, text):
            calls["count"] += 1
            # Trip immediately rather than letting the quadratic version grind
            # through several minutes of CPU before the assertion below.
            assert calls["count"] <= scan_limit, (
                f"relevant-file regex ran {calls['count']} times for "
                f"{message_count} messages: the budget search is re-scanning "
                "history per candidate selection"
            )
            return real_pattern.findall(text)

    monkeypatch.setattr(summary_module, "_PATH_PATTERN", _CountingPattern())

    messages = [
        Message(
            role=MessageRole.USER if index % 2 == 0 else MessageRole.ASSISTANT,
            parts=[MessagePart.text_part("word " * 1_000)],
        )
        for index in range(message_count)
    ]
    strategy = BudgetCompactionStrategy(
        budget=ContextBudget(max_chars=SOL_MAX_CHARS, reserve_chars=SOL_RESERVE_CHARS)
    )

    result = strategy.compact(messages)

    assert result.compacted is True
    assert calls["count"] <= scan_limit
