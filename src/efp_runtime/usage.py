"""Provider-neutral usage telemetry helpers for Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union


@dataclass
class UsageSummary:
    """Normalized token and cost summary for one or more provider steps."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    cost_usd: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.input_tokens = _coerce_token_count(self.input_tokens)
        self.output_tokens = _coerce_token_count(self.output_tokens)
        self.reasoning_tokens = _coerce_token_count(self.reasoning_tokens)
        self.cached_input_tokens = _coerce_token_count(self.cached_input_tokens)
        self.total_tokens = _coerce_token_count(self.total_tokens)
        self.cost_usd = None if self.cost_usd is None else float(self.cost_usd)
        self.metadata = dict(self.metadata)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "metadata": dict(self.metadata),
        }


UsageInput = Union[UsageSummary, Mapping[str, Any]]


def normalize_usage(raw: Mapping[str, Any]) -> UsageSummary:
    """Normalize common provider token fields into a UsageSummary."""

    input_tokens = _first_token_count(raw, "input_tokens", "prompt_tokens")
    output_tokens = _first_token_count(raw, "output_tokens", "completion_tokens")
    reasoning_tokens = _first_token_count(
        raw,
        "reasoning_tokens",
        "reasoning_output_tokens",
    )
    cached_input_tokens = _first_token_count(raw, "cached_input_tokens")

    prompt_details = _mapping(raw.get("prompt_tokens_details"))
    input_details = _mapping(raw.get("input_tokens_details"))
    output_details = _mapping(raw.get("output_tokens_details"))
    completion_details = _mapping(raw.get("completion_tokens_details"))
    if cached_input_tokens == 0:
        cached_input_tokens = _first_token_count(
            input_details,
            "cached_input_tokens",
            "cached_tokens",
        )
    if cached_input_tokens == 0:
        cached_input_tokens = _first_token_count(
            prompt_details,
            "cached_input_tokens",
            "cached_tokens",
        )
    if reasoning_tokens == 0:
        reasoning_tokens = _first_token_count(
            output_details,
            "reasoning_tokens",
            "reasoning_output_tokens",
        )
    if reasoning_tokens == 0:
        reasoning_tokens = _first_token_count(
            completion_details,
            "reasoning_tokens",
            "reasoning_output_tokens",
        )

    total_tokens = _first_token_count(raw, "total_tokens")
    if total_tokens == 0:
        total_tokens = input_tokens + output_tokens + reasoning_tokens

    metadata = {
        key: value
        for key, value in raw.items()
        if key not in _KNOWN_USAGE_FIELDS
    }
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=total_tokens,
        metadata=metadata,
    )


def merge_usage(summaries: Iterable[UsageInput]) -> UsageSummary:
    """Merge normalized usage summaries into one accumulated summary."""

    merged = UsageSummary()
    cost_usd = 0.0
    has_cost = False
    metadata: Dict[str, Any] = {}
    for item in summaries:
        summary = item if isinstance(item, UsageSummary) else normalize_usage(item)
        merged.input_tokens += summary.input_tokens
        merged.output_tokens += summary.output_tokens
        merged.reasoning_tokens += summary.reasoning_tokens
        merged.cached_input_tokens += summary.cached_input_tokens
        merged.total_tokens += summary.total_tokens
        if summary.cost_usd is not None:
            cost_usd += float(summary.cost_usd)
            has_cost = True
        metadata.update(summary.metadata)
    merged.cost_usd = cost_usd if has_cost else None
    merged.metadata = metadata
    return merged


def estimate_cost(
    summary: UsageSummary,
    pricing: Mapping[str, Any],
) -> Optional[float]:
    """Estimate cost from caller-provided per-1M token prices."""

    normalized_pricing = validate_usage_pricing(pricing)
    if not normalized_pricing:
        return None
    return (
        summary.input_tokens * normalized_pricing.get("input_per_1m", 0.0)
        + summary.output_tokens * normalized_pricing.get("output_per_1m", 0.0)
        + summary.reasoning_tokens * normalized_pricing.get("reasoning_per_1m", 0.0)
        + summary.cached_input_tokens
        * normalized_pricing.get("cached_input_per_1m", 0.0)
    ) / 1_000_000.0


def validate_usage_pricing(pricing: Mapping[str, Any]) -> Dict[str, float]:
    """Return a validated copy of caller-provided usage pricing."""

    normalized: Dict[str, float] = {}
    for key, value in dict(pricing or {}).items():
        if isinstance(value, bool):
            raise ValueError(f"usage_pricing[{key!r}] must be greater than or equal to 0")
        try:
            price = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"usage_pricing[{key!r}] must be greater than or equal to 0"
            ) from exc
        if price < 0:
            raise ValueError(f"usage_pricing[{key!r}] must be greater than or equal to 0")
        normalized[str(key)] = price
    return normalized


def _first_token_count(raw: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        if key not in raw:
            continue
        value = _coerce_token_count(raw.get(key))
        if value:
            return value
    return 0


def _coerce_token_count(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


_KNOWN_USAGE_FIELDS = {
    "input_tokens",
    "prompt_tokens",
    "output_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "reasoning_output_tokens",
    "cached_input_tokens",
    "total_tokens",
    "prompt_tokens_details",
    "input_tokens_details",
    "output_tokens_details",
    "completion_tokens_details",
}


__all__ = [
    "UsageSummary",
    "estimate_cost",
    "merge_usage",
    "normalize_usage",
    "validate_usage_pricing",
]
