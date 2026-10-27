"""GitHub Copilot AI-credit pricing for the models the runtime sends.

Copilot has billed per token since 2026-06-01: a request's input, cached
input and output tokens are priced at the model's published rates and the
total is converted into GitHub AI credits (1 credit = $0.01). The GPT-6 and
GPT-5.6 lines have two tiers, chosen per request by how many input tokens the
request carries: ``default`` up to the threshold (272,000 for the GPT-6 line,
200,000 for GPT-5.6 Luna) and ``long_context`` above it at about twice the
price. Reasoning tokens are output tokens: OpenAI counts them inside
``output_tokens`` and reports them again under ``output_tokens_details``.

The prices below are USD per million tokens, read on 2026-10-27 from
https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing.
Copilot's ``GET /models`` catalog carries the same numbers under
``billing.token_prices`` (in credits per million tokens, with the default
tier's ``max_prompt_tokens`` as the threshold); a run that has fetched the
catalog registers that row here (``register_catalog_pricing``) so a price
change on GitHub's side reaches the estimate without a release.

The estimate is an estimate. GitHub bills cache writes separately, but the
Responses API only reports cache reads, so uncached input is priced at the
input rate. The absolute numbers should be checked against the GitHub billing
report once; the relative picture (iterations times context size) is what the
Portal shows members, and that is exact either way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


USD_PER_CREDIT = 0.01

DEFAULT_TIER = "default"
LONG_CONTEXT_TIER = "long_context"


@dataclass(frozen=True)
class PriceTier:
    """One pricing tier of a model, USD per million tokens."""

    name: str
    input_usd_per_1m: float
    cached_input_usd_per_1m: float
    output_usd_per_1m: float
    cache_write_usd_per_1m: float | None = None


@dataclass(frozen=True)
class ModelPricing:
    """How Copilot prices one model: a default tier and an optional long-context tier."""

    model_id: str
    default: PriceTier
    long_context: PriceTier | None = None
    # Input tokens per request above which ``long_context`` applies.
    long_context_threshold_tokens: int | None = None
    source: str = "static"

    def tier_for(self, input_tokens: int | None) -> PriceTier:
        """The tier a request with ``input_tokens`` prompt tokens is billed at."""

        if (
            self.long_context is not None
            and self.long_context_threshold_tokens is not None
            and input_tokens is not None
            and input_tokens > self.long_context_threshold_tokens
        ):
            return self.long_context
        return self.default


@dataclass(frozen=True)
class CreditEstimate:
    """Estimated AI credits for one or more model steps."""

    credits: float
    usd: float
    model_id: str
    tier: str
    threshold_tokens: int | None
    source: str

    def payload(self) -> dict[str, Any]:
        return {
            "model": self.model_id,
            "tier": self.tier,
            "threshold_tokens": self.threshold_tokens,
            "source": self.source,
            "unit": "credits",
            "usd_per_credit": USD_PER_CREDIT,
        }


def _tiers(
    model_id: str,
    *,
    default: tuple[float, float, float, float],
    long_context: tuple[float, float, float, float] | None = None,
    threshold: int | None = None,
) -> ModelPricing:
    """Build a static row; tuples are (input, cached input, cache write, output)."""

    def tier(name: str, prices: tuple[float, float, float, float]) -> PriceTier:
        input_price, cached_price, write_price, output_price = prices
        return PriceTier(
            name=name,
            input_usd_per_1m=input_price,
            cached_input_usd_per_1m=cached_price,
            cache_write_usd_per_1m=write_price,
            output_usd_per_1m=output_price,
        )

    return ModelPricing(
        model_id=model_id,
        default=tier(DEFAULT_TIER, default),
        long_context=tier(LONG_CONTEXT_TIER, long_context) if long_context else None,
        long_context_threshold_tokens=threshold,
        source="static",
    )


GPT6_THRESHOLD_TOKENS = 272_000

# (input, cached input, cache write, output) in USD per 1M tokens.
_STATIC_PRICING: dict[str, ModelPricing] = {
    "gpt-6.1-sol": _tiers(
        "gpt-6.1-sol",
        default=(2.00, 0.10, 2.50, 10.00),
        long_context=(4.00, 0.20, 5.00, 15.00),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-6.1-terra": _tiers(
        "gpt-6.1-terra",
        default=(1.00, 0.05, 1.25, 5.00),
        long_context=(2.00, 0.10, 2.50, 7.50),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-6-sol": _tiers(
        "gpt-6-sol",
        default=(2.00, 0.20, 2.50, 10.00),
        long_context=(4.00, 0.40, 5.00, 15.00),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-6-luna": _tiers(
        "gpt-6-luna",
        default=(0.10, 0.01, 0.125, 0.50),
        long_context=(0.20, 0.02, 0.25, 0.75),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-6-astra": _tiers(
        "gpt-6-astra",
        default=(10.00, 1.00, 12.50, 50.00),
        long_context=(20.00, 2.00, 25.00, 75.00),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-5.6-sol": _tiers(
        "gpt-5.6-sol",
        default=(4.00, 0.40, 5.00, 20.00),
        long_context=(8.00, 0.80, 10.00, 30.00),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-5.6-terra": _tiers(
        "gpt-5.6-terra",
        default=(2.00, 0.20, 2.50, 12.00),
        long_context=(4.00, 0.40, 5.00, 18.00),
        threshold=GPT6_THRESHOLD_TOKENS,
    ),
    "gpt-5.6-luna": _tiers(
        "gpt-5.6-luna",
        default=(0.20, 0.02, 0.25, 1.20),
        long_context=(0.40, 0.04, 0.50, 1.80),
        threshold=200_000,
    ),
    "gpt-5.4-nano": _tiers("gpt-5.4-nano", default=(0.20, 0.02, 0.0, 1.25)),
    "gpt-5.3-codex": _tiers("gpt-5.3-codex", default=(1.75, 0.175, 0.0, 14.00)),
    "mai-code-1.1-flash": _tiers("mai-code-1.1-flash", default=(0.20, 0.02, 0.0, 1.20)),
}

# Rows learned from Copilot's catalog at run time, keyed by model id.
_CATALOG_PRICING: dict[str, ModelPricing] = {}

_CATALOG_INPUT_KEYS = ("input_price", "input", "prompt_price", "prompt")
_CATALOG_OUTPUT_KEYS = ("output_price", "output", "completion_price", "completion")
_CATALOG_CACHED_KEYS = (
    "cached_input_price",
    "cache_read_price",
    "cached_price",
    "input_cached_price",
    "cached_input",
)
_CATALOG_CACHE_WRITE_KEYS = ("cache_write_price", "cache_creation_price", "cache_write")


def normalize_model_id(model: Any) -> str | None:
    """``github-copilot/GPT-6 Sol`` -> ``gpt-6-sol``; None for anything but a non-empty string."""

    if not isinstance(model, str):
        return None
    text = model.strip().lower()
    if not text:
        return None
    if "/" in text:
        text = text.split("/", 1)[1]
    text = "-".join(text.split())
    return text or None


def pricing_for_model(model: Any) -> ModelPricing | None:
    """The pricing row for ``model`` (catalog-learned first, then static), or None."""

    model_id = normalize_model_id(model)
    if model_id is None:
        return None
    learned = _CATALOG_PRICING.get(model_id)
    if learned is not None:
        return learned
    return _STATIC_PRICING.get(model_id)


def billing_threshold_tokens(model: Any) -> int | None:
    """Input tokens per request above which ``model`` is billed at the long-context tier."""

    pricing = pricing_for_model(model)
    if pricing is None or pricing.long_context is None:
        return None
    return pricing.long_context_threshold_tokens


def billing_tier_for(model: Any, input_tokens: int | None) -> str | None:
    """``default`` or ``long_context`` for a request with ``input_tokens``; None for an unpriced model."""

    pricing = pricing_for_model(model)
    if pricing is None:
        return None
    return pricing.tier_for(input_tokens).name


def estimate_credits(
    model: Any,
    *,
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> CreditEstimate | None:
    """Estimate the AI credits one model step (or a sum of steps at one tier) costs.

    ``input_tokens`` is the prompt as the API bills it, cached tokens included;
    ``cached_input_tokens`` and ``cache_write_tokens`` are the parts of it the
    provider reported as cache reads and cache writes. Reasoning tokens are
    only added when the provider did not already count them inside
    ``output_tokens`` (OpenAI does). None for a model this module cannot price.
    """

    pricing = pricing_for_model(model)
    if pricing is None:
        return None
    prompt = _non_negative(input_tokens)
    cached = min(_non_negative(cached_input_tokens), prompt)
    written = min(_non_negative(cache_write_tokens), max(0, prompt - cached))
    uncached = max(0, prompt - cached - written)
    output = _non_negative(output_tokens)
    reasoning = _non_negative(reasoning_tokens)
    billable_output = output if output >= reasoning else output + reasoning

    tier = pricing.tier_for(prompt)
    write_price = (
        tier.cache_write_usd_per_1m
        if tier.cache_write_usd_per_1m
        else tier.input_usd_per_1m
    )
    usd = (
        uncached * tier.input_usd_per_1m
        + cached * tier.cached_input_usd_per_1m
        + written * write_price
        + billable_output * tier.output_usd_per_1m
    ) / 1_000_000.0
    return CreditEstimate(
        credits=round(usd / USD_PER_CREDIT, 4),
        usd=round(usd, 6),
        model_id=pricing.model_id,
        tier=tier.name,
        threshold_tokens=pricing.long_context_threshold_tokens,
        source=pricing.source,
    )


def estimate_credits_for_usage(model: Any, usage: Mapping[str, Any]) -> CreditEstimate | None:
    """``estimate_credits`` over a normalized usage mapping (``UsageSummary.to_dict()`` shape)."""

    if not isinstance(usage, Mapping):
        return None
    return estimate_credits(
        model,
        input_tokens=_int(usage.get("input_tokens")),
        cached_input_tokens=_int(usage.get("cached_input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        reasoning_tokens=_int(usage.get("reasoning_tokens")),
        cache_write_tokens=_int(usage.get("cache_write_tokens")),
    )


def register_catalog_pricing(model: Any, entry: Any) -> ModelPricing | None:
    """Learn a model's prices from its ``GET /models`` row; the row then wins the lookup.

    The catalog prices are credits per million tokens (``input_price: 200``
    is $2.00); a row whose numbers look like USD instead is read as USD when
    a static row exists to compare against. Keys the catalog lacks (cached
    input, cache write) keep the static row's values, or a tenth of the
    input price for cached input when there is no static row. None when the
    row has no usable prices.
    """

    model_id = normalize_model_id(model)
    if model_id is None or not isinstance(entry, Mapping):
        return None
    billing = entry.get("billing")
    if not isinstance(billing, Mapping):
        return None
    token_prices = billing.get("token_prices")
    if not isinstance(token_prices, Mapping):
        return None
    static = _STATIC_PRICING.get(model_id)
    default_tier = _catalog_tier(
        token_prices.get(DEFAULT_TIER),
        name=DEFAULT_TIER,
        reference=static.default if static else None,
    )
    if default_tier is None:
        return None
    long_tier = _catalog_tier(
        token_prices.get(LONG_CONTEXT_TIER),
        name=LONG_CONTEXT_TIER,
        reference=static.long_context if static else None,
    )
    threshold = None
    default_row = token_prices.get(DEFAULT_TIER)
    if isinstance(default_row, Mapping):
        threshold = _positive_int(default_row.get("max_prompt_tokens"))
    if threshold is None and static is not None:
        threshold = static.long_context_threshold_tokens
    pricing = ModelPricing(
        model_id=model_id,
        default=default_tier,
        long_context=long_tier,
        long_context_threshold_tokens=threshold if long_tier is not None else None,
        source="catalog",
    )
    _CATALOG_PRICING[model_id] = pricing
    return pricing


def clear_catalog_pricing() -> None:
    """Forget every catalog-learned row (tests)."""

    _CATALOG_PRICING.clear()


def known_model_ids() -> tuple[str, ...]:
    return tuple(sorted({*_STATIC_PRICING, *_CATALOG_PRICING}))


def _catalog_tier(
    row: Any,
    *,
    name: str,
    reference: PriceTier | None,
) -> PriceTier | None:
    if not isinstance(row, Mapping):
        return None
    input_price = _catalog_price(
        row,
        _CATALOG_INPUT_KEYS,
        reference=reference.input_usd_per_1m if reference else None,
    )
    output_price = _catalog_price(
        row,
        _CATALOG_OUTPUT_KEYS,
        reference=reference.output_usd_per_1m if reference else None,
    )
    if input_price is None or output_price is None:
        return None
    cached_price = _catalog_price(
        row,
        _CATALOG_CACHED_KEYS,
        reference=reference.cached_input_usd_per_1m if reference else None,
    )
    if cached_price is None:
        cached_price = (
            reference.cached_input_usd_per_1m if reference else round(input_price / 10.0, 6)
        )
    write_price = _catalog_price(
        row,
        _CATALOG_CACHE_WRITE_KEYS,
        reference=reference.cache_write_usd_per_1m if reference else None,
    )
    if write_price is None and reference is not None:
        write_price = reference.cache_write_usd_per_1m
    return PriceTier(
        name=name,
        input_usd_per_1m=input_price,
        cached_input_usd_per_1m=cached_price,
        cache_write_usd_per_1m=write_price,
        output_usd_per_1m=output_price,
    )


def _catalog_price(
    row: Mapping[str, Any],
    keys: tuple[str, ...],
    *,
    reference: float | None,
) -> float | None:
    for key in keys:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < 0:
            continue
        return _scale_catalog_price(float(value), reference)
    return None


def _scale_catalog_price(value: float, reference: float | None) -> float:
    """Credits per 1M (the catalog's unit) to USD per 1M, unless USD fits the static row better."""

    as_cents = value / 100.0
    if reference is None or reference <= 0:
        return round(as_cents, 6)
    if abs(as_cents - reference) <= abs(value - reference):
        return round(as_cents, 6)
    return round(value, 6)


def _non_negative(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 1:
        return None
    return value


__all__ = [
    "CreditEstimate",
    "DEFAULT_TIER",
    "GPT6_THRESHOLD_TOKENS",
    "LONG_CONTEXT_TIER",
    "ModelPricing",
    "PriceTier",
    "USD_PER_CREDIT",
    "billing_threshold_tokens",
    "billing_tier_for",
    "clear_catalog_pricing",
    "estimate_credits",
    "estimate_credits_for_usage",
    "known_model_ids",
    "normalize_model_id",
    "pricing_for_model",
    "register_catalog_pricing",
]
