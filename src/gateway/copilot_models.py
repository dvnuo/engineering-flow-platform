"""The Copilot models this runtime can offer (``GET /api/models``).

Copilot's own catalog, ``GET {capi}/models`` with the exchanged Copilot
token, is where VS Code's model picker comes from: one row per model with
``capabilities.limits`` (context window, prompt cap, output cap),
``capabilities.supports`` (tool calls, vision, the reasoning efforts it
takes), ``supported_endpoints``, ``policy.state`` (the organization's model
policy), ``model_picker_enabled`` and ``billing.token_prices`` (the default
and long-context price tiers, in credits per million tokens). The native
runtime speaks only the Responses API and needs tool calls, so a row is
*selectable* when it is a chat model served on ``/responses`` with tool
calls, its policy enabled and the picker flag set: the same rule the auto
routing applies to the model Copilot picks.

Selectable ids widen the model allowlist (``register_catalog_model_ids``),
their limits become context profiles and their prices feed the credits
estimate, so a model GitHub adds reaches the Portal picker and the budget
without a code change. The hand-maintained list is the offline default:
without a credential, or when the catalog cannot be fetched, the payload says
``source: "static"``. Answers are cached per credential for an hour, failures
for a few minutes.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.efp_runtime.llm.models import (
    AI_PLATFORM_MODEL_IDS,
    COPILOT_AUTO_TIERS,
    DEFAULT_AI_PLATFORM_MODEL,
    DEFAULT_MODEL_ID,
    SUPPORTED_COPILOT_MODEL_IDS,
    copilot_model_context_profile,
    register_catalog_model_ids,
    register_model_context_profile,
    static_model_context_profile,
)
from src.efp_runtime.llm.pricing import USD_PER_CREDIT, pricing_for_model, register_catalog_pricing
from src.gateway.copilot_quota import _runtime_credential

logger = logging.getLogger(__name__)

SOURCE_CATALOG = "catalog"
SOURCE_STATIC = "static"
COPILOT_PROVIDER = "github-copilot"
AI_PLATFORM_PROVIDER = "ai_platform"
CACHE_TTL_SECONDS = 3600.0
FAILURE_TTL_SECONDS = 300.0
AUTO_LABELS = {
    "auto": "Auto",
    "auto-efficiency": "Auto (Efficiency)",
    "auto-intelligence": "Auto (Intelligence)",
}
_RESPONSES_PATH = "/responses"
# ``billing.token_prices`` keys, as pricing.py reads them: credits per million tokens.
_PRICE_KEYS = {
    "input": ("input_price", "input", "prompt_price", "prompt"),
    "output": ("output_price", "output", "completion_price", "completion"),
    "cached_input": ("cached_input_price", "cached_price", "cached_input", "cached", "cache_read_price"),
    "cache_write": ("cache_write_price", "cache_creation_price", "cache_write"),
}
_COPILOT_PREFIXES = ("github-copilot/", "github_copilot/", "copilot/")

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def copilot_models_for_runtime(*, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """The models payload for this runtime's credential (cached), or the static list."""

    provider = _configured_provider()
    if provider in {"ai_platform", "ai-platform"}:
        return {**static_models_payload(provider=AI_PLATFORM_PROVIDER), "cached": False}
    credential = _runtime_credential()
    if not credential:
        return {**static_models_payload(error="no Copilot credential configured"), "cached": False}
    key = hashlib.sha256(credential.encode("utf-8")).hexdigest()
    current = time.monotonic() if now is None else now
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None and not force and current < cached[0]:
            return {**cached[1], "cached": True}
    try:
        catalog = _fetch_catalog(force=force)
    except Exception as exc:  # noqa: BLE001 - the static list stands in; the reason rides along.
        reason = str(exc).strip() or exc.__class__.__name__
        logger.warning("Copilot models catalog unavailable: %s", reason)
        payload = static_models_payload(error="catalog unavailable: {0}".format(reason[:300]))
        ttl = FAILURE_TTL_SECONDS
    else:
        payload = models_payload_from_catalog(catalog)
        ttl = CACHE_TTL_SECONDS
    with _cache_lock:
        _cache[key] = (current + ttl, payload)
    return {**payload, "cached": False}


def cached_models_payload() -> dict[str, Any] | None:
    """The most recent payload built for any credential, without fetching; None when none."""

    with _cache_lock:
        if not _cache:
            return None
        _expires, payload = max(_cache.values(), key=lambda item: item[0])
    return dict(payload)


def admit_model_from_catalog(value: Any, *, fetch: bool = True) -> str | None:
    """The canonical id when the catalog lists ``value`` as a model this runtime can drive, else None.

    Called by the gateway when a requested model is not on the static list:
    with ``fetch`` the catalog is (re)loaded through the cache, so a fresh pod
    accepts a catalog id on its first chat; without it only an already cached
    answer is consulted.
    """

    model_id = _normalize_model_id(value)
    if not model_id or model_id in COPILOT_AUTO_TIERS:
        return None
    payload = copilot_models_for_runtime() if fetch else cached_models_payload()
    if not isinstance(payload, Mapping) or payload.get("source") != SOURCE_CATALOG:
        return None
    selectable = payload.get("selectable")
    if isinstance(selectable, list) and model_id in selectable:
        register_catalog_model_ids([model_id])
        return model_id
    return None


def clear_models_cache() -> None:
    with _cache_lock:
        _cache.clear()


def models_payload_from_catalog(
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Project Copilot's ``GET /models`` rows onto the payload, admitting the selectable ones."""

    rows: list[dict[str, Any]] = []
    selectable: list[str] = []
    for raw_id in sorted(catalog, key=lambda item: str(item)):
        row = catalog[raw_id]
        if not isinstance(row, Mapping):
            continue
        entry = model_entry(raw_id, row)
        if not entry["id"]:
            continue
        rows.append(entry)
        if not entry["selectable"]:
            continue
        selectable.append(entry["id"])
        profile = copilot_model_context_profile({**row, "id": entry["id"]})
        if profile is not None:
            register_model_context_profile(profile)
        try:
            register_catalog_pricing(entry["id"], row)
        except Exception:  # noqa: BLE001 - the static price row stands in.
            logger.debug("Catalog pricing for %s not registered", entry["id"], exc_info=True)
    register_catalog_model_ids(selectable)
    return {
        "available": True,
        "source": SOURCE_CATALOG,
        "fetched_at": fetched_at or _now_iso(),
        "provider": COPILOT_PROVIDER,
        "default_model": DEFAULT_MODEL_ID,
        "auto": auto_entries(),
        "models": rows,
        "selectable": selectable,
        "error": None,
    }


def model_entry(raw_id: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """One catalog row as the Portal reads it, with why it is (not) selectable."""

    model_id = _normalize_model_id(raw_id) or _normalize_model_id(row.get("id"))
    capabilities = _mapping(row.get("capabilities"))
    limits = _mapping(capabilities.get("limits"))
    supports = _mapping(capabilities.get("supports"))
    policy = _mapping(row.get("policy"))
    billing = _mapping(row.get("billing"))
    endpoints = [
        str(entry).strip()
        for entry in (row.get("supported_endpoints") or [])
        if isinstance(entry, str) and str(entry).strip()
    ]
    reasons: list[str] = []
    kind = str(capabilities.get("type") or "").strip().lower()
    if kind and kind != "chat":
        reasons.append("not a chat model ({0})".format(kind))
    if endpoints and not any(_endpoint_path(entry).startswith(_RESPONSES_PATH) for entry in endpoints):
        reasons.append("not served on {0}".format(_RESPONSES_PATH))
    if supports.get("tool_calls") is False:
        reasons.append("no tool calls")
    state = str(policy.get("state") or "").strip().lower()
    if state and state != "enabled":
        reasons.append("policy {0}".format(state))
    picker = row.get("model_picker_enabled")
    if picker is False:
        reasons.append("hidden from the picker")
    efforts_raw = supports.get("reasoning_effort")
    efforts = (
        [str(effort).strip().lower() for effort in efforts_raw if isinstance(effort, str) and effort.strip()]
        if isinstance(efforts_raw, list)
        else None
    )
    pricing = _catalog_pricing(billing)
    return {
        "id": model_id,
        "name": str(row.get("name") or model_id or "").strip() or model_id,
        "vendor": _text(row.get("vendor")),
        "version": _text(row.get("version")),
        "preview": bool(row.get("preview")) if isinstance(row.get("preview"), bool) else None,
        "selectable": not reasons,
        "reasons": reasons,
        "known": model_id in SUPPORTED_COPILOT_MODEL_IDS,
        "picker_enabled": picker if isinstance(picker, bool) else None,
        "policy": state or None,
        "category": _text(row.get("model_picker_category")),
        "limits": {
            "context_window_tokens": _positive_int(limits.get("max_context_window_tokens")),
            "prompt_tokens": _positive_int(limits.get("max_prompt_tokens")),
            "output_tokens": _positive_int(limits.get("max_output_tokens")),
        },
        "supports": {
            "tool_calls": _flag(supports.get("tool_calls")),
            "vision": _flag(supports.get("vision")),
            "parallel_tool_calls": _flag(supports.get("parallel_tool_calls")),
            "streaming": _flag(supports.get("streaming")),
            "structured_outputs": _flag(supports.get("structured_outputs")),
            "reasoning_efforts": efforts,
        },
        "endpoints": endpoints,
        "pricing": pricing,
        "billing_threshold_tokens": _billing_threshold(pricing),
    }


def static_models_payload(
    *,
    provider: str = COPILOT_PROVIDER,
    error: str | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """The hand-maintained list, shaped like the catalog payload."""

    if provider in {AI_PLATFORM_PROVIDER, "ai-platform"}:
        models = [_static_entry(model_id, pricing=False) for model_id in AI_PLATFORM_MODEL_IDS]
        return {
            "available": True,
            "source": SOURCE_STATIC,
            "fetched_at": fetched_at or _now_iso(),
            "provider": AI_PLATFORM_PROVIDER,
            "default_model": DEFAULT_AI_PLATFORM_MODEL,
            "auto": [],
            "models": models,
            "selectable": list(AI_PLATFORM_MODEL_IDS),
            "error": error,
        }
    models = [_static_entry(model_id) for model_id in SUPPORTED_COPILOT_MODEL_IDS]
    return {
        "available": True,
        "source": SOURCE_STATIC,
        "fetched_at": fetched_at or _now_iso(),
        "provider": COPILOT_PROVIDER,
        "default_model": DEFAULT_MODEL_ID,
        "auto": auto_entries(),
        "models": models,
        "selectable": list(SUPPORTED_COPILOT_MODEL_IDS),
        "error": error,
    }


def auto_entries() -> list[dict[str, Any]]:
    return [
        {"id": auto_id, "label": AUTO_LABELS.get(auto_id, auto_id), "tier": tier}
        for auto_id, tier in COPILOT_AUTO_TIERS.items()
    ]


def _static_entry(model_id: str, *, pricing: bool = True) -> dict[str, Any]:
    profile = static_model_context_profile(model_id)
    window = getattr(profile, "context_window_tokens", None)
    reserve = getattr(profile, "default_reserve_tokens", None)
    prompt = window - reserve if isinstance(window, int) and isinstance(reserve, int) and window > reserve else None
    price_payload: dict[str, Any] | None = None
    threshold: int | None = None
    if pricing:
        row = pricing_for_model(model_id)
        if row is not None:
            price_payload = {"default": _static_tier(row.default)}
            if row.long_context is not None:
                price_payload["long_context"] = _static_tier(row.long_context)
                price_payload["default"]["prompt_cap_tokens"] = row.long_context_threshold_tokens
            threshold = row.long_context_threshold_tokens
    return {
        "id": model_id,
        "name": model_id,
        "vendor": None,
        "version": None,
        "preview": None,
        "selectable": True,
        "reasons": [],
        "known": True,
        "picker_enabled": None,
        "policy": None,
        "category": None,
        "limits": {
            "context_window_tokens": window if isinstance(window, int) else None,
            "prompt_tokens": prompt,
            "output_tokens": reserve if isinstance(reserve, int) else None,
        },
        "supports": {
            "tool_calls": True,
            "vision": None,
            "parallel_tool_calls": None,
            "streaming": None,
            "structured_outputs": None,
            "reasoning_efforts": None,
        },
        "endpoints": [_RESPONSES_PATH],
        "pricing": price_payload,
        "billing_threshold_tokens": threshold,
    }


def _static_tier(tier: Any) -> dict[str, Any]:
    def credits(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return round(float(value) / USD_PER_CREDIT, 4)
        except (TypeError, ValueError):
            return None

    return {
        "prompt_cap_tokens": None,
        "input": credits(getattr(tier, "input_usd_per_1m", None)),
        "output": credits(getattr(tier, "output_usd_per_1m", None)),
        "cached_input": credits(getattr(tier, "cached_input_usd_per_1m", None)),
        "cache_write": credits(getattr(tier, "cache_write_usd_per_1m", None)),
    }


def _catalog_pricing(billing: Mapping[str, Any]) -> dict[str, Any] | None:
    prices = _mapping(billing.get("token_prices"))
    if not prices:
        return None
    payload: dict[str, Any] = {}
    for tier_name, tier in prices.items():
        if not isinstance(tier, Mapping):
            continue
        payload[str(tier_name)] = {
            "prompt_cap_tokens": _positive_int(tier.get("max_prompt_tokens")),
            **{label: _number_from(tier, keys) for label, keys in _PRICE_KEYS.items()},
        }
    return payload or None


def _billing_threshold(pricing: Mapping[str, Any] | None) -> int | None:
    """The default tier's prompt cap, when a long-context tier exists to bill above it."""

    if not pricing or "long_context" not in pricing:
        return None
    default_tier = pricing.get("default")
    if not isinstance(default_tier, Mapping):
        return None
    cap = default_tier.get("prompt_cap_tokens")
    return cap if isinstance(cap, int) and cap > 0 else None


def _fetch_catalog(*, force: bool) -> Mapping[str, Mapping[str, Any]]:
    """Copilot's catalog through the configured provider's transport (its own hourly cache)."""

    from src.gateway.runtime_chat import _build_llm_provider

    provider, _model = _build_llm_provider(None)
    transport = getattr(provider, "transport", None)
    lookup = getattr(transport, "models_catalog", None)
    if not callable(lookup):
        raise RuntimeError("the configured provider has no model catalog")
    catalog = lookup(force=force)
    if not isinstance(catalog, Mapping) or not catalog:
        raise RuntimeError("Copilot returned no model catalog")
    return catalog


def _configured_provider() -> str:
    try:
        from src.config import config

        llm = config.llm if isinstance(config.llm, Mapping) else {}
    except Exception:  # noqa: BLE001 - no config means the Copilot default.
        return ""
    return str(llm.get("provider") or "").strip().lower()


def _normalize_model_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    for prefix in _COPILOT_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def _endpoint_path(entry: str) -> str:
    text = str(entry or "").strip()
    return text[text.index("/"):] if "/" in text else text


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _flag(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _number_from(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CACHE_TTL_SECONDS",
    "FAILURE_TTL_SECONDS",
    "SOURCE_CATALOG",
    "SOURCE_STATIC",
    "admit_model_from_catalog",
    "auto_entries",
    "cached_models_payload",
    "clear_models_cache",
    "copilot_models_for_runtime",
    "model_entry",
    "models_payload_from_catalog",
    "static_models_payload",
]
