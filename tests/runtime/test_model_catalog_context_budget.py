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


SOL_WINDOW_TOKENS = 1_000_000
SOL_SAFETY_TOKENS = 8_000
SOL_MAX_CHARS = (SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS) * 4  # 3_968_000
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
        ("gpt-5.6-sol", 1_000_000),
        ("gpt-5.6-terra", 1_000_000),
        ("gpt-5.6-luna", 1_000_000),
        ("gpt-5.4", 1_000_000),
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
    """ai_platform serves gpt-5.4 with the same 1M window as Copilot.

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


def test_runner_rejects_zero_and_bool_context_size_caps():
    """The runner constructor is a second, hand-written copy of these guards.

    RuntimeConfig and RuntimeLoopRunner validate the same knobs in separate
    code; without a mirror test here they can drift apart again.
    """

    for field in ["max_context_tokens", "max_context_chars", "max_context_parts"]:
        for value in [0, -1, True, "5"]:
            with pytest.raises(ValueError, match=field):
                _runner(
                    InMemorySessionStore(),
                    ScriptedLLMProvider([]),
                    **{field: value},
                )


# --------------------------------------------------------------------------
# ...but an explicit budget larger than the model can take is clamped
# --------------------------------------------------------------------------


def test_over_window_max_context_tokens_is_clamped_to_the_catalog_ceiling():
    """10M tokens on a 1M model reproduces the exact 400 the default prevents.

    Nothing compared the explicit value against the model window, so an
    over-window override sailed straight through to the provider.
    """

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=10_000_000,
    )

    budget = runner._context_budget()

    assert budget.max_chars == SOL_MAX_CHARS  # was 40_000_000
    assert budget.reserve_chars == SOL_RESERVE_CHARS
    assert budget.effective_max_chars == SOL_MAX_CHARS - SOL_RESERVE_CHARS
    # Clamping lands on the same budget as no override at all, not a third value.
    assert budget.max_chars == _runner(
        InMemorySessionStore(), ScriptedLLMProvider([])
    )._context_budget().max_chars


def test_over_window_max_context_chars_is_clamped_too():
    """The chars route is unbounded in exactly the same way.

    But the clamp bounds the CAP, not the final prompt, and the two routes hold
    back different reserves (see
    test_tokens_and_chars_routes_keep_their_asymmetric_reserves). So a clamped
    max_context_chars does NOT land on the unset path's effective budget the way
    a clamped max_context_tokens does - it keeps its zero reserve and authorises
    the whole ceiling. That is in-spec (the ceiling already excludes the safety
    margin, so it is still inside the declared window) but it is not parity, and
    it is pinned here so it cannot change unnoticed.
    """

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=10_000_000,
    )

    budget = runner._context_budget()

    assert budget.max_chars == SOL_MAX_CHARS  # was 10_000_000
    assert budget.reserve_chars == 0
    assert budget.effective_max_chars == SOL_MAX_CHARS
    # ~992_000 tokens: below the 1_000_000 window and above the prompt budget
    # left after the response reserve on the token route.
    assert budget.effective_max_chars // 4 == SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS
    assert budget.effective_max_chars > (SOL_MAX_CHARS - SOL_RESERVE_CHARS)


def test_clamped_chars_route_metadata_reports_the_in_force_token_numbers():
    """The clamp fills in two token fields the chars route otherwise leaves None.

    A consumer reading ``max_context_tokens`` off a chars-configured runtime
    gets ``None`` normally and an int once the clamp fires, because the catalog
    ceiling - which is token-denominated - has become the budget. Pinned so the
    type change is a decision rather than a surprise.
    """

    unclamped = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=1_000_000,
    )._model_context_budget_metadata()
    clamped = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=10_000_000,
    )._model_context_budget_metadata()

    assert unclamped["context_safety_margin_tokens"] is None
    assert unclamped["max_context_tokens"] is None
    assert unclamped["context_budget"]["max_context_chars_clamped"] is False

    assert clamped["context_safety_margin_tokens"] == SOL_SAFETY_TOKENS
    assert clamped["max_context_tokens"] == SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS
    assert clamped["context_budget"]["max_context_chars_clamped"] is True
    # The source still names the knob the operator actually set.
    assert clamped["context_budget"]["max_context_chars_source"] == "max_context_chars"
    assert clamped["context_budget"]["max_context_chars_requested"] == 10_000_000


def test_a_budget_exactly_at_the_ceiling_is_not_clamped():
    """Pins the comparison as ``>``, not ``>=``.

    Otherwise the largest legal budget would report itself as clamped and drag
    the token metadata fields along with it.
    """

    at_ceiling = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=SOL_MAX_CHARS,
    )._model_context_budget_metadata()

    assert at_ceiling["context_budget"]["max_context_chars_clamped"] is False
    assert at_ceiling["context_budget"]["max_chars"] == SOL_MAX_CHARS
    assert at_ceiling["max_context_tokens"] is None

    one_over = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=SOL_MAX_CHARS + 1,
    )._model_context_budget_metadata()

    assert one_over["context_budget"]["max_context_chars_clamped"] is True
    assert one_over["context_budget"]["max_chars"] == SOL_MAX_CHARS


def test_clamp_uses_each_models_own_window():
    """The ceiling is per-profile, resolved per request, not a constant."""

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_model="gpt-5.6-luna",
        max_context_tokens=10_000_000,
    )

    assert runner._context_budget().max_chars == (1_000_000 - 8_000) * 4


def test_unknown_model_override_is_never_clamped():
    """The required design: the fallback profile is exempt.

    A newly released or gateway-only model resolves to the 64k conservative
    fallback, and an operator override is the only way to correct that guess.
    Clamping it to the fallback ceiling would defeat the override's only
    purpose, so an uncatalogued model's explicit budget is honoured verbatim.
    """

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_model="some-unlisted-model",
        max_context_tokens=1_000_000,
    )

    budget = runner._context_budget()

    # The fallback ceiling would have been (64_000 - 3_200) * 4 == 243_200.
    assert budget.max_chars == 4_000_000
    assert budget.effective_max_chars == 4_000_000 - 4_000 * 4


@pytest.mark.parametrize(
    ("kwargs", "expected_max_chars"),
    [
        ({"max_context_tokens": 250_000}, 1_000_000),
        ({"max_context_chars": 1_000_000}, 1_000_000),
        ({"max_context_tokens": 50_000}, 200_000),
        ({"max_context_chars": 100_000}, 100_000),
    ],
)
def test_sub_window_explicit_budgets_are_untouched_by_the_clamp(
    kwargs, expected_max_chars
):
    runner = _runner(InMemorySessionStore(), ScriptedLLMProvider([]), **kwargs)

    assert runner._context_budget().max_chars == expected_max_chars


def test_clamped_budget_metadata_records_the_clamp():
    """A clamp must never be silent - the operator's number stops being used."""

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=10_000_000,
    )

    metadata = runner._model_context_budget_metadata()
    context_budget = metadata["context_budget"]

    assert context_budget["max_context_chars_source"] == "max_context_tokens"
    assert context_budget["max_context_chars_requested"] == 40_000_000
    assert context_budget["max_context_chars_limit"] == SOL_MAX_CHARS
    assert context_budget["max_context_chars_clamped"] is True
    assert context_budget["max_chars"] == SOL_MAX_CHARS
    # The token-denominated fields describe the budget actually in force, not
    # the rejected request; the original stays visible as ..._requested.
    assert metadata["context_safety_margin_tokens"] == SOL_SAFETY_TOKENS
    assert metadata["max_context_tokens"] == SOL_WINDOW_TOKENS - SOL_SAFETY_TOKENS


def test_unclamped_budget_metadata_reports_no_clamp():
    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=250_000,
    )

    context_budget = runner._model_context_budget_metadata()["context_budget"]

    assert context_budget["max_context_chars_requested"] == 1_000_000
    assert context_budget["max_context_chars_limit"] == SOL_MAX_CHARS
    assert context_budget["max_context_chars_clamped"] is False
    assert runner._model_context_budget_metadata()["max_context_tokens"] == 250_000


def test_fallback_profile_metadata_reports_no_ceiling_at_all():
    """An uncatalogued model advertises that no ceiling was applicable.

    ``max_context_chars_limit is None`` alone does not distinguish this from
    "nothing configured" - the unset case reports None too (see
    test_unset_budget_metadata_has_nothing_to_clamp). The pair does: a non-null
    ``_requested`` next to a null ``_limit`` means "an override was set and
    deliberately not clamped", which is the state an operator debugging an
    uncatalogued model needs to see.
    """

    runner = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        default_model="some-unlisted-model",
        max_context_tokens=1_000_000,
    )

    context_budget = runner._model_context_budget_metadata()["context_budget"]

    assert context_budget["max_context_chars_source"] == "max_context_tokens"
    assert context_budget["max_context_chars_requested"] == 4_000_000
    assert context_budget["max_context_chars_limit"] is None
    assert context_budget["max_context_chars_clamped"] is False


def test_unset_budget_metadata_has_nothing_to_clamp():
    context_budget = _runner(
        InMemorySessionStore(), ScriptedLLMProvider([])
    )._model_context_budget_metadata()["context_budget"]

    assert context_budget["max_context_chars_requested"] is None
    assert context_budget["max_context_chars_limit"] is None
    assert context_budget["max_context_chars_clamped"] is False


def test_tokens_and_chars_routes_keep_their_asymmetric_reserves():
    """Pins a deliberate asymmetry. Do NOT "fix" this test by equalising them.

    ``max_context_tokens`` is read as a context WINDOW and has the model's
    declared response reserve subtracted; ``max_context_chars`` is read as a
    prompt BUDGET and has nothing subtracted. So the same nominal size yields a
    2x different prompt. This predates the model-catalog default (it arrived
    with the Runtime v2 baseline, #521) and both directions of changing it are
    behaviour changes to explicitly-configured deployments, so #583 documented
    the real numbers in config.yaml.example instead of altering them. This test
    exists so the asymmetry cannot drift silently in either direction.
    """

    tokens_budget = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=250_000,
    )._context_budget()
    chars_budget = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_chars=1_000_000,
    )._context_budget()

    assert tokens_budget.max_chars == 1_000_000
    assert tokens_budget.reserve_chars == 500_000  # 512_000 reserve, clamped to half
    assert tokens_budget.effective_max_chars == 500_000  # ~125_000 tokens

    assert chars_budget.max_chars == 1_000_000
    assert chars_budget.reserve_chars == 0
    assert chars_budget.effective_max_chars == 1_000_000  # ~250_000 tokens

    assert chars_budget.effective_max_chars == 2 * tokens_budget.effective_max_chars


# --------------------------------------------------------------------------
# The reserve may never swallow the budget
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"context_reserve_tokens": 600_000},
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
    _seed_large_history(store, "session-big", messages=40, chars=4_500_000)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-big", user_text="latest request")

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.prepared_request.compaction_applied is True
    assert _request_chars(request.provider_request) < 4_500_000
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
    _seed_large_history(store, "session-keep", messages=40, chars=4_500_000)
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
    _seed_large_history(store, "session-event", messages=40, chars=4_500_000)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider)

    result = await runner.run(session_id="session-event", user_text="latest request")

    events = [
        event for event in result.runtime_events if event.type == "request_compacted"
    ]
    assert len(events) == 1
    assert events[0].payload["stored"] is False
    assert events[0].payload["scope"] == "request"
    assert events[0].payload["compacted_message_count"] > 0

    # "session_compacted" means the stored session was rewritten. Render-time
    # trimming must never claim that, or a consumer cannot tell whether
    # anything on disk changed.
    assert not [
        event for event in result.runtime_events if event.type == "session_compacted"
    ]


@pytest.mark.asyncio
async def test_over_window_override_produces_a_bounded_request_end_to_end():
    """The clamp has to bind a real provider request, not just a private method.

    Every other clamp test pokes ``_context_budget()``. This one proves the
    clamped ceiling actually reaches ``prepare_history_for_request``: without
    the clamp a 10M-token override sends the whole 4.5M-char history verbatim,
    which is the provider 400 the catalog default exists to prevent.
    """

    store = InMemorySessionStore()
    _seed_large_history(store, "session-over-window", messages=40, chars=4_500_000)
    provider = ScriptedLLMProvider([{"content": "Done."}])
    runner = _runner(store, provider, max_context_tokens=10_000_000)

    result = await runner.run(
        session_id="session-over-window",
        user_text="latest request",
    )

    assert result.status == LoopStatus.COMPLETED
    request = provider.requests[0]
    assert request.prepared_request.compaction_applied is True
    assert _request_chars(request.provider_request) < SOL_MAX_CHARS
    assert (
        request.prepared_request.compaction_metadata["kept_chars"]
        <= SOL_MAX_CHARS - SOL_RESERVE_CHARS
    )
    # ...and the clamp is legible on the event the compaction emits.
    events = [
        event for event in result.runtime_events if event.type == "request_compacted"
    ]
    assert events[0].payload["context_budget"]["max_context_chars_clamped"] is True
    assert events[0].payload["context_budget"]["max_context_chars_requested"] == (
        40_000_000
    )
    # The size knob alone still does not touch the transcript.
    assert not [
        event for event in result.runtime_events if event.type == "session_compacted"
    ]


@pytest.mark.parametrize("rewrite_stored_history", [True, False])
def test_budget_metadata_reports_whether_stored_history_gets_rewritten(
    rewrite_stored_history,
):
    """The rewrite gate must be readable, not inferable from missing events.

    Its only other signal is the ABSENCE of session_compaction_started /
    session_compacted, which tells an operator nothing unless they already knew
    the flag existed - and the deployment it changes most (a size knob set,
    stored rewriting now off, session files growing) is exactly the one that
    would not.
    """

    context_budget = _runner(
        InMemorySessionStore(),
        ScriptedLLMProvider([]),
        max_context_tokens=50_000,
        compaction_rewrite_stored_history=rewrite_stored_history,
    )._model_context_budget_metadata()["context_budget"]

    assert context_budget["stored_history_rewrite"] is rewrite_stored_history


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
        event
        for event in result.runtime_events
        if event.type in {"session_compacted", "request_compacted"}
    ]


# --------------------------------------------------------------------------
# The budget search must not block the event loop
# --------------------------------------------------------------------------


def test_budget_search_scans_each_message_once(monkeypatch):
    """Guards the quadratic re-scan that made this default unusable.

    The budget search re-derives the compaction summary for every candidate
    selection, and rendering that summary scans every compacted message's text
    with the relevant-file regex. Without a per-call memo that is O(blocks x
    history chars): a multi-megabyte / 600 block history took ~170 s of synchronous,
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
            parts=[MessagePart.text_part("word " * 1_500)],
        )
        for index in range(message_count)
    ]
    strategy = BudgetCompactionStrategy(
        budget=ContextBudget(max_chars=SOL_MAX_CHARS, reserve_chars=SOL_RESERVE_CHARS)
    )

    result = strategy.compact(messages)

    assert result.compacted is True
    assert calls["count"] <= scan_limit
