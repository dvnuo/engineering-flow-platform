"""Copilot AI-credit pricing: tiers, cached input, reasoning, catalog rows."""

from __future__ import annotations

import pytest

from efp_runtime.llm.pricing import (
    billing_threshold_tokens,
    billing_tier_for,
    clear_catalog_pricing,
    estimate_credits,
    estimate_credits_for_usage,
    pricing_for_model,
    register_catalog_pricing,
)


@pytest.fixture(autouse=True)
def _clean_catalog():
    clear_catalog_pricing()
    yield
    clear_catalog_pricing()


def test_default_tier_prices_gpt_61_sol():
    # 200k prompt tokens: under the 272k threshold, so the default tier.
    estimate = estimate_credits("gpt-6.1-sol", input_tokens=200_000, output_tokens=100_000)
    assert estimate is not None
    assert estimate.tier == "default"
    assert estimate.usd == pytest.approx(0.4 + 1.0)
    assert estimate.credits == pytest.approx(140.0)
    assert estimate.threshold_tokens == 272_000
    assert estimate.payload()["unit"] == "credits"


def test_cached_input_is_billed_at_the_cached_rate():
    estimate = estimate_credits("gpt-6.1-sol", input_tokens=200_000, cached_input_tokens=150_000)
    assert estimate is not None
    assert estimate.usd == pytest.approx(50_000 * 2.0 / 1e6 + 150_000 * 0.10 / 1e6)


def test_long_context_tier_above_the_threshold():
    estimate = estimate_credits("gpt-6.1-sol", input_tokens=300_000)
    assert estimate is not None
    assert estimate.tier == "long_context"
    assert estimate.usd == pytest.approx(1.2)
    assert billing_tier_for("gpt-6.1-sol", 272_000) == "default"
    assert billing_tier_for("gpt-6.1-sol", 272_001) == "long_context"
    assert billing_threshold_tokens("gpt-6.1-sol") == 272_000
    assert billing_threshold_tokens("gpt-5.6-luna") == 200_000
    assert billing_threshold_tokens("mai-code-1.1-flash") is None


def test_reasoning_tokens_inside_output_are_not_double_counted():
    inclusive = estimate_credits("gpt-6-sol", output_tokens=100, reasoning_tokens=40)
    assert inclusive is not None
    assert inclusive.usd == pytest.approx(100 * 10.0 / 1e6)
    separate = estimate_credits("gpt-6-sol", output_tokens=30, reasoning_tokens=40)
    assert separate is not None
    assert separate.usd == pytest.approx(70 * 10.0 / 1e6)


def test_model_ids_are_normalized_and_unknown_models_are_unpriced():
    row = pricing_for_model("github-copilot/GPT-6 Sol")
    assert row is not None and row.model_id == "gpt-6-sol"
    assert estimate_credits("auto") is None
    assert estimate_credits("claude-opus-5") is None
    assert billing_tier_for("gpt-5.4", 10) is None
    assert estimate_credits(None) is None


def test_estimate_for_usage_mapping():
    estimate = estimate_credits_for_usage(
        "gpt-6-luna",
        {"input_tokens": 100_000, "cached_input_tokens": 0, "output_tokens": 0},
    )
    assert estimate is not None
    assert estimate.credits == pytest.approx(1.0)
    # A whole-prompt count above the threshold is the long-context tier.
    long_context = estimate_credits_for_usage("gpt-6-luna", {"input_tokens": 1_000_000})
    assert long_context is not None
    assert long_context.tier == "long_context"
    assert long_context.credits == pytest.approx(20.0)
    assert estimate_credits_for_usage("gpt-6-luna", "not a mapping") is None


def test_catalog_row_overrides_static_prices_and_keeps_missing_rates():
    row = {
        "id": "gpt-6.1-sol",
        "billing": {
            "token_prices": {
                "default": {"max_prompt_tokens": 272_000, "input_price": 300, "output_price": 1200},
                "long_context": {"max_prompt_tokens": 922_000, "input_price": 600, "output_price": 1800},
            }
        },
    }
    learned = register_catalog_pricing("gpt-6.1-sol", row)
    assert learned is not None
    assert learned.source == "catalog"
    assert learned.default.input_usd_per_1m == pytest.approx(3.0)
    # The catalog said nothing about cached input: the static rate stands in.
    assert learned.default.cached_input_usd_per_1m == pytest.approx(0.10)
    assert learned.long_context is not None
    assert learned.long_context.output_usd_per_1m == pytest.approx(18.0)
    assert learned.long_context_threshold_tokens == 272_000
    assert pricing_for_model("gpt-6.1-sol") is learned
    estimate = estimate_credits("gpt-6.1-sol", input_tokens=200_000)
    assert estimate is not None and estimate.source == "catalog" and estimate.usd == pytest.approx(0.6)
    clear_catalog_pricing()
    assert pricing_for_model("gpt-6.1-sol").source == "static"


def test_catalog_prices_already_in_usd_are_recognised():
    row = {"billing": {"token_prices": {"default": {"input_price": 2.0, "output_price": 10.0}}}}
    learned = register_catalog_pricing("gpt-6-sol", row)
    assert learned is not None
    assert learned.default.input_usd_per_1m == pytest.approx(2.0)
    assert learned.long_context is None
    assert billing_threshold_tokens("gpt-6-sol") is None


def test_catalog_row_without_prices_is_ignored():
    assert register_catalog_pricing("gpt-6-sol", {"billing": {}}) is None
    assert register_catalog_pricing("gpt-6-sol", {"billing": {"token_prices": {"default": {}}}}) is None
    assert register_catalog_pricing(None, {}) is None
    assert pricing_for_model("gpt-6-sol").source == "static"
