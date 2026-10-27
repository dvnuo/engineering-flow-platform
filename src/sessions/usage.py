"""Usage tracking for Engineering Flow Platform.

Tracks token usage and costs across all providers, with per-session,
per-model and per-provider breakdowns.

Costs: a model GitHub Copilot bills in AI credits is priced from GitHub's
published per-token rates (``efp_runtime.llm.pricing``: two tiers by prompt
size, cached input at its own rate, reasoning counted as output); a model that
table does not know falls back to the legacy per-model prices below, and
anything else to ``default``. Every record also keeps the cached and reasoning
token counts and the credits estimate, so the Portal's usage views can show
where the credits went rather than a bare token total.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.efp_runtime.llm.pricing import estimate_credits

logger = logging.getLogger(__name__)

# Legacy per-model prices (USD per 1M tokens) for models the Copilot price
# table does not cover; "default" is the last resort.
TOKEN_PRICING = {
    # OpenAI models
    "gpt-4o": {"input": 5.0, "output": 15.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    "gpt-4": {"input": 30.0, "output": 60.0},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    "gpt-3.5-turbo-16k": {"input": 3.0, "output": 4.0},
    "o1-preview": {"input": 15.0, "output": 60.0},
    "o1-mini": {"input": 1.10, "output": 4.40},
    # Anthropic models
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-20250514": {"input": 0.25, "output": 1.25},
    "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
    "claude-3-opus": {"input": 15.0, "output": 75.0},
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    # GitHub Copilot without a model id (old records)
    "github_copilot": {"input": 5.0, "output": 15.0},
    # Default fallback
    "default": {"input": 1.0, "output": 2.0},
}

# Provider mapping
PROVIDER_MODEL_MAP = {
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o1-preview", "o1-mini"],
    "anthropic": ["claude-sonnet-4-20250514", "claude-opus-4-20250514", "claude-haiku-4-20250514"],
    "github_copilot": [
        "github_copilot",
        "gpt-6-astra",
        "gpt-6-luna",
        "gpt-6-sol",
        "gpt-6.1-sol",
        "auto",
        "auto-efficiency",
        "auto-intelligence",
    ],
    "ollama": ["default"],
}


@dataclass
class UsageStats:
    """Token usage statistics for one recorded run."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    context_tokens: int = 0
    model: str = ""
    cost: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    # GitHub AI credits (1 credit = $0.01) from the published Copilot prices;
    # None for a model without a price row.
    estimated_credits: Optional[float] = None
    pricing_tier: Optional[str] = None
    iterations: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "context_tokens": self.context_tokens,
            "model": self.model,
            "cost": self.cost,
            "timestamp": self.timestamp,
            "cached_input_tokens": self.cached_input_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "estimated_credits": self.estimated_credits,
            "pricing_tier": self.pricing_tier,
            "iterations": self.iterations,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UsageStats":
        return cls(
            input_tokens=_int(data.get("input_tokens")),
            output_tokens=_int(data.get("output_tokens")),
            total_tokens=_int(data.get("total_tokens")),
            context_tokens=_int(data.get("context_tokens")),
            model=data.get("model", ""),
            cost=_float(data.get("cost")) or 0.0,
            timestamp=data.get("timestamp", datetime.utcnow().isoformat()),
            cached_input_tokens=_int(data.get("cached_input_tokens")),
            reasoning_tokens=_int(data.get("reasoning_tokens")),
            estimated_credits=_float(data.get("estimated_credits")),
            pricing_tier=data.get("pricing_tier") or None,
            iterations=_optional_int(data.get("iterations")),
        )


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float:
    """Estimate the USD cost for a model and token counts.

    Copilot-priced models use GitHub's published rates (tier by prompt size,
    cached input at its own rate); everything else uses the legacy table.
    """

    estimate = estimate_credits(
        model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    if estimate is not None:
        return estimate.usd
    pricing = TOKEN_PRICING.get(model, TOKEN_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return input_cost + output_cost


def estimate_usage_credits(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> tuple[Optional[float], Optional[str]]:
    """(credits, pricing tier) for a Copilot-priced model, (None, None) otherwise."""

    estimate = estimate_credits(
        model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )
    if estimate is None:
        return None, None
    return estimate.credits, estimate.tier


_EMPTY_TOTALS = {
    "total_input": 0,
    "total_output": 0,
    "total_tokens": 0,
    "total_cost": 0.0,
    "total_cost_usd": 0.0,
    "total_cached_input_tokens": 0,
    "total_reasoning_tokens": 0,
    "total_estimated_credits": 0.0,
    "request_count": 0,
}


class UsageTracker:
    """Tracks token usage per session and globally."""

    def __init__(self, base_path: str = "~/.efp/usage"):
        self.base_path = Path(base_path).expanduser()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.session_file = self.base_path / "sessions.jsonl"
        self.global_file = self.base_path / "global.jsonl"

    def _parse_usage_from_response(self, response: Dict, model: str) -> UsageStats:
        """Parse token usage from an LLM API response (OpenAI chat or Responses shape)."""
        usage = response.get("usage", {}) or {}

        input_tokens = _int(usage.get("prompt_tokens")) or _int(usage.get("input_tokens"))
        output_tokens = _int(usage.get("completion_tokens")) or _int(usage.get("output_tokens"))
        total_tokens = _int(usage.get("total_tokens")) or (input_tokens + output_tokens)
        cached_input_tokens = _int(usage.get("cached_input_tokens"))
        if not cached_input_tokens:
            for details_key in ("input_tokens_details", "prompt_tokens_details"):
                details = usage.get(details_key)
                if isinstance(details, dict):
                    cached_input_tokens = _int(details.get("cached_tokens"))
                    if cached_input_tokens:
                        break
        reasoning_tokens = _int(usage.get("reasoning_tokens"))
        if not reasoning_tokens:
            for details_key in ("output_tokens_details", "completion_tokens_details"):
                details = usage.get(details_key)
                if isinstance(details, dict):
                    reasoning_tokens = _int(details.get("reasoning_tokens"))
                    if reasoning_tokens:
                        break

        return self._stats(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    def _stats(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: Optional[int] = None,
        cached_input_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_credits: Optional[float] = None,
        pricing_tier: Optional[str] = None,
        iterations: Optional[int] = None,
    ) -> UsageStats:
        credits = _float(estimated_credits)
        tier = pricing_tier or None
        if credits is None:
            credits, computed_tier = estimate_usage_credits(
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                reasoning_tokens=reasoning_tokens,
            )
            tier = tier or computed_tier
        cost = estimate_cost(
            model,
            input_tokens,
            output_tokens,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        return UsageStats(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens if total_tokens is not None else input_tokens + output_tokens,
            # Context tokens approximate the prompt size of the last request.
            context_tokens=input_tokens,
            model=model,
            cost=cost,
            cached_input_tokens=cached_input_tokens,
            reasoning_tokens=reasoning_tokens,
            estimated_credits=credits,
            pricing_tier=tier,
            iterations=_optional_int(iterations),
        )

    def record_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        session_id: str = "default",
        task_type: str = "chat",
        *,
        cached_input_tokens: int = 0,
        reasoning_tokens: int = 0,
        estimated_credits: Optional[float] = None,
        pricing_tier: Optional[str] = None,
        iterations: Optional[int] = None,
    ) -> UsageStats:
        """Record token usage for a session.

        ``input_tokens`` is the prompt as billed (cached tokens included);
        ``estimated_credits`` / ``pricing_tier`` are taken from the caller when
        the run already priced its steps, else computed here from the totals.
        """
        stats = self._stats(
            model=model,
            input_tokens=_int(input_tokens),
            output_tokens=_int(output_tokens),
            cached_input_tokens=_int(cached_input_tokens),
            reasoning_tokens=_int(reasoning_tokens),
            estimated_credits=estimated_credits,
            pricing_tier=pricing_tier,
            iterations=iterations,
        )
        usage_entry = {
            "type": "usage",
            "provider": provider,
            "session_id": session_id,
            "task_type": task_type,
            **stats.to_dict(),
        }
        with open(self.session_file, "a", encoding="utf-8") as session_handle:
            session_handle.write(json.dumps(usage_entry) + "\n")
        with open(self.global_file, "a", encoding="utf-8") as global_handle:
            global_handle.write(json.dumps(usage_entry) + "\n")
        return stats

    def get_session_usage(self, session_id: str) -> List[UsageStats]:
        """Get usage stats for a specific session."""
        usages = []

        if not self.session_file.exists():
            return usages

        with open(self.session_file, 'r', encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        usages.append(UsageStats.from_dict(entry))

        return usages

    def get_session_summary(self, session_id: str) -> Dict[str, Any]:
        """Get summary stats for a session."""
        usages = self.get_session_usage(session_id)

        summary: Dict[str, Any] = {"session_id": session_id, **_EMPTY_TOTALS, "model": ""}
        if not usages:
            return summary

        for usage in usages:
            _add_totals(summary, usage.to_dict())
        summary["model"] = usages[-1].model
        return summary

    def get_global_summary(self, hours: Optional[int] = None) -> Dict[str, Any]:
        """Get global usage summary, optionally filtered by time."""
        summary: Dict[str, Any] = dict(_EMPTY_TOTALS)
        for entry in self._global_entries(hours):
            _add_totals(summary, entry)
        return summary

    def get_usage_by_model(self, hours: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """Get usage breakdown by model."""
        by_model: Dict[str, Dict[str, Any]] = {}
        for entry in self._global_entries(hours, usage_only=True):
            model = entry.get("model", "unknown")
            bucket = by_model.setdefault(model, _empty_bucket())
            _add_bucket(bucket, entry)
        return by_model

    def get_usage_by_provider(self, hours: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
        """Get usage breakdown by provider."""
        by_provider: Dict[str, Dict[str, Any]] = {}

        # Infer provider from model
        def infer_provider(model: str) -> str:
            for provider, models in PROVIDER_MODEL_MAP.items():
                if model in models or model.startswith(provider):
                    return provider
            return "unknown"

        for entry in self._global_entries(hours, usage_only=True):
            model = entry.get("model", "unknown")
            provider = entry.get("provider") or infer_provider(model)
            bucket = by_provider.setdefault(provider, _empty_bucket())
            _add_bucket(bucket, entry)
        return by_provider

    def _global_entries(self, hours: Optional[int], *, usage_only: bool = False):
        if not self.global_file.exists():
            return
        cutoff = None
        if hours:
            cutoff = datetime.utcnow().timestamp() - (hours * 3600)
        with open(self.global_file, 'r', encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if usage_only and entry.get("type") != "usage":
                    continue
                if cutoff:
                    try:
                        ts = datetime.fromisoformat(entry["timestamp"]).timestamp()
                    except (KeyError, TypeError, ValueError):
                        continue
                    if ts < cutoff:
                        continue
                yield entry

    def cleanup_old_records(self, retention_days: int = 30) -> int:
        """Remove records older than retention_days.

        Args:
            retention_days: Number of days to retain

        Returns:
            Number of records removed
        """
        if not self.global_file.exists():
            return 0

        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        remaining = []
        removed = 0

        with open(self.global_file, 'r', encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    ts = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
                    if ts >= cutoff:
                        remaining.append(line)
                    else:
                        removed += 1

        with open(self.global_file, 'w', encoding="utf-8") as f:
            for line in remaining:
                f.write(line)

        if removed > 0:
            logger.info(f"Cleaned up {removed} old usage records")

        return removed

    def health_check(self) -> Dict[str, Any]:
        """Check health status of usage tracking.

        Returns:
            Health status dictionary
        """
        try:
            stats = self.get_global_summary(hours=24)
            by_model = self.get_usage_by_model(hours=24)
            by_provider = self.get_usage_by_provider(hours=24)
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
            }

        return {
            "status": "healthy",
            "storage_dir": str(self.base_path),
            "session_file": str(self.session_file),
            "global_file": str(self.global_file),
            "last_24h": stats,
            "by_model": by_model,
            "by_provider": by_provider,
        }

    def clear_session_usage(self, session_id: str) -> int:
        """Clear usage records for a session. Returns count cleared."""
        if not self.session_file.exists():
            return 0

        remaining = []
        cleared = 0

        with open(self.session_file, 'r', encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("session_id") == session_id:
                        cleared += 1
                    else:
                        remaining.append(line)

        with open(self.session_file, 'w', encoding="utf-8") as f:
            f.writelines(remaining)

        return cleared


def _empty_bucket() -> Dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "cost": 0.0,
        "estimated_credits": 0.0,
        "requests": 0,
    }


def _add_bucket(bucket: Dict[str, Any], entry: Dict[str, Any]) -> None:
    bucket["input_tokens"] += _int(entry.get("input_tokens"))
    bucket["output_tokens"] += _int(entry.get("output_tokens"))
    bucket["cached_input_tokens"] += _int(entry.get("cached_input_tokens"))
    bucket["reasoning_tokens"] += _int(entry.get("reasoning_tokens"))
    bucket["cost"] += _float(entry.get("cost")) or 0.0
    bucket["estimated_credits"] += _float(entry.get("estimated_credits")) or 0.0
    bucket["requests"] += 1


def _add_totals(summary: Dict[str, Any], entry: Dict[str, Any]) -> None:
    input_tokens = _int(entry.get("input_tokens"))
    output_tokens = _int(entry.get("output_tokens"))
    cost = _float(entry.get("cost")) or 0.0
    summary["total_input"] += input_tokens
    summary["total_output"] += output_tokens
    summary["total_tokens"] += input_tokens + output_tokens
    summary["total_cost"] += cost
    summary["total_cost_usd"] += cost
    summary["total_cached_input_tokens"] += _int(entry.get("cached_input_tokens"))
    summary["total_reasoning_tokens"] += _int(entry.get("reasoning_tokens"))
    summary["total_estimated_credits"] += _float(entry.get("estimated_credits")) or 0.0
    summary["request_count"] += 1


def _int(value: Any) -> int:
    if value is None or isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# Global usage tracker instance
usage_tracker = UsageTracker()
