"""The usage tracker behind GET /api/usage: Copilot prices, cached and reasoning tokens, credits."""

from __future__ import annotations

import tempfile

import pytest

from src.sessions.usage import UsageTracker, estimate_cost


def test_copilot_models_are_priced_from_the_published_table():
    assert estimate_cost("gpt-6.1-sol", 200_000, 0) == pytest.approx(0.4)
    assert estimate_cost("gpt-6.1-sol", 200_000, 0, cached_input_tokens=200_000) == pytest.approx(0.02)
    # Above 272k prompt tokens the long-context tier doubles the input price.
    assert estimate_cost("gpt-6.1-sol", 300_000, 0) == pytest.approx(1.2)
    # Models outside the Copilot table keep the legacy prices.
    assert estimate_cost("gpt-4o", 1_000_000, 1_000_000) == 20.0
    assert estimate_cost("something-else", 1_000_000, 0) == 1.0


def test_record_usage_keeps_cached_reasoning_and_credits():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        stats = tracker.record_usage(
            provider="github_copilot",
            model="gpt-6-sol",
            input_tokens=100_000,
            output_tokens=2_000,
            session_id="s1",
            cached_input_tokens=60_000,
            reasoning_tokens=1_500,
            iterations=7,
        )
        assert stats.cached_input_tokens == 60_000
        assert stats.reasoning_tokens == 1_500
        assert stats.iterations == 7
        expected_usd = (40_000 * 2.0 + 60_000 * 0.20 + 2_000 * 10.0) / 1e6
        assert stats.cost == pytest.approx(expected_usd)
        assert stats.estimated_credits == pytest.approx(expected_usd * 100)
        assert stats.pricing_tier == "default"

        tracker.record_usage(
            provider="github_copilot",
            model="gpt-6-sol",
            input_tokens=10,
            output_tokens=1,
            session_id="s1",
            estimated_credits=12.5,
            pricing_tier="long_context",
        )

        summary = tracker.get_session_summary("s1")
        assert summary["request_count"] == 2
        assert summary["total_input"] == 100_010
        assert summary["total_cached_input_tokens"] == 60_000
        assert summary["total_reasoning_tokens"] == 1_500
        assert summary["total_estimated_credits"] == pytest.approx(expected_usd * 100 + 12.5)
        assert summary["model"] == "gpt-6-sol"

        global_summary = tracker.get_global_summary(hours=24)
        assert global_summary["request_count"] == 2
        assert global_summary["total_estimated_credits"] == pytest.approx(summary["total_estimated_credits"])
        assert global_summary["total_cost_usd"] == pytest.approx(global_summary["total_cost"])

        by_model = tracker.get_usage_by_model(hours=24)["gpt-6-sol"]
        assert by_model["requests"] == 2
        assert by_model["cached_input_tokens"] == 60_000
        assert by_model["reasoning_tokens"] == 1_500
        assert by_model["estimated_credits"] == pytest.approx(summary["total_estimated_credits"])

        by_provider = tracker.get_usage_by_provider(hours=24)
        assert by_provider["github_copilot"]["requests"] == 2


def test_record_usage_without_a_priced_model_has_no_credits():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        stats = tracker.record_usage(
            provider="ai_platform",
            model="gpt-4o",
            input_tokens=1_000,
            output_tokens=100,
            session_id="s2",
        )
        assert stats.estimated_credits is None
        assert stats.pricing_tier is None
        assert stats.cost == pytest.approx((1_000 * 5.0 + 100 * 15.0) / 1e6)
        # No credits is not zero credits: the Portal shows the dollar estimate then.
        assert tracker.get_session_summary("s2")["total_estimated_credits"] is None


def test_record_usage_on_ai_platform_has_no_credits_for_a_model_copilot_prices():
    # GPT-6 Sol is served by both providers and has a Copilot price row. A run
    # that brought no credits (an AI Platform run never does) used to be
    # priced here from that row, whatever the record's provider.
    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        tokens = {"model": "gpt-6-sol", "input_tokens": 300_000, "output_tokens": 1_000}
        on_ai_platform = tracker.record_usage(provider="ai_platform", session_id="ai", **tokens)
        assert on_ai_platform.estimated_credits is None
        assert on_ai_platform.pricing_tier is None
        # No credits total at all, in every view of the record.
        assert tracker.get_session_summary("ai")["total_estimated_credits"] is None
        assert tracker.get_global_summary(hours=24)["total_estimated_credits"] is None
        assert tracker.get_usage_by_provider(hours=24)["ai_platform"]["estimated_credits"] is None
        assert tracker.get_usage_by_model(hours=24)["gpt-6-sol"]["estimated_credits"] is None
        # The tokens are still booked, and the dollar figure stays a list-price estimate.
        assert tracker.get_session_summary("ai")["total_input"] == 300_000
        assert tracker.get_session_summary("ai")["total_cost_usd"] > 0

        for copilot in ("github_copilot", "github-copilot", ""):
            priced = tracker.record_usage(provider=copilot, session_id=f"copilot-{copilot}", **tokens)
            assert priced.estimated_credits > 0
            assert priced.pricing_tier == "long_context"

        # Credits a caller did price are kept as given.
        kept = tracker.record_usage(provider="ai_platform", session_id="given", estimated_credits=1.5, **tokens)
        assert kept.estimated_credits == 1.5
        # A total counts the records that carry credits and skips the ones that do not.
        by_provider = tracker.get_usage_by_provider(hours=24)
        assert by_provider["ai_platform"]["estimated_credits"] == 1.5
        assert by_provider["ai_platform"]["requests"] == 2
        assert tracker.get_usage_by_model(hours=24)["gpt-6-sol"]["estimated_credits"] > 1.5


def test_parse_usage_from_response_reads_the_responses_api_shape():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        stats = tracker._parse_usage_from_response(
            {
                "usage": {
                    "input_tokens": 500,
                    "output_tokens": 50,
                    "input_tokens_details": {"cached_tokens": 200},
                    "output_tokens_details": {"reasoning_tokens": 20},
                }
            },
            "gpt-6-luna",
        )
        assert stats.input_tokens == 500
        assert stats.output_tokens == 50
        assert stats.total_tokens == 550
        assert stats.cached_input_tokens == 200
        assert stats.reasoning_tokens == 20
        assert stats.estimated_credits is not None


def test_old_records_without_the_new_fields_still_summarize():
    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        legacy = (
            '{"type": "usage", "provider": "github_copilot", "session_id": "old", '
            '"task_type": "chat", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15, '
            '"context_tokens": 10, "model": "github_copilot", "cost": 0.0001, '
            '"timestamp": "2099-01-01T00:00:00"}\n'
        )
        tracker.session_file.write_text(legacy, encoding="utf-8")
        tracker.global_file.write_text(legacy, encoding="utf-8")
        summary = tracker.get_session_summary("old")
        assert summary["total_input"] == 10
        assert summary["total_cached_input_tokens"] == 0
        assert summary["total_estimated_credits"] is None
        assert tracker.get_global_summary()["request_count"] == 1


def test_the_chat_endpoint_books_an_ai_platform_run_without_credits(monkeypatch):
    # What /api/chat does with a finished run's usage (runtime_api._record_chat_usage),
    # which is what GET /api/usage and the Portal's Usage panel then read.
    from src.gateway import runtime_api

    with tempfile.TemporaryDirectory() as tmp:
        tracker = UsageTracker(f"{tmp}/usage")
        monkeypatch.setattr(runtime_api, "usage_tracker", tracker)

        # An AI Platform run as the runner reports it: tokens, no credits, no pricing.
        monkeypatch.setattr(
            runtime_api.global_config,
            "_config",
            {"llm": {"provider": "ai_platform", "model": "gpt-6-sol"}},
            raising=False,
        )
        runtime_api._record_chat_usage(
            {"input_tokens": 300_000, "output_tokens": 1_000, "estimated_credits": None, "iterations": 2},
            model="gpt-6-sol",
            session_id="chat-ai",
        )
        summary = tracker.get_session_summary("chat-ai")
        assert summary["request_count"] == 1
        assert summary["total_input"] == 300_000
        assert summary["total_estimated_credits"] is None
        by_provider = tracker.get_usage_by_provider(hours=24)
        assert by_provider["ai_platform"]["requests"] == 1
        assert by_provider["ai_platform"]["estimated_credits"] is None

        # The same turn on Copilot keeps the credits the runner priced it at.
        monkeypatch.setattr(
            runtime_api.global_config,
            "_config",
            {"llm": {"provider": "github_copilot", "model": "gpt-6-sol"}},
            raising=False,
        )
        runtime_api._record_chat_usage(
            {
                "input_tokens": 300_000,
                "output_tokens": 1_000,
                "estimated_credits": 123.0,
                "pricing": {"model": "gpt-6-sol", "tier": "long_context"},
            },
            model="gpt-6-sol",
            session_id="chat-copilot",
        )
        assert tracker.get_session_summary("chat-copilot")["total_estimated_credits"] == 123.0
