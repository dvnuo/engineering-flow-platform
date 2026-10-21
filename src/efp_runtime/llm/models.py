"""Copilot-first model context profiles for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any


DEFAULT_PROVIDER_ID = "github-copilot"
DEFAULT_MODEL_ID = "gpt-6-sol"
DEFAULT_CHARS_PER_TOKEN = 4
MIN_PRESERVE_RECENT_TOKENS = 2_000
MAX_PRESERVE_RECENT_TOKENS = 8_000
DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS = 8_000
CONTEXT_SAFETY_MARGIN_WINDOW_DIVISOR = 20
# GitHub Copilot models a member can pick for the native runtime: the GPT-6
# line (Astra, Sol, Luna) plus GPT-6.1 Sol, which joined on 2026-09-29.
# gpt-5.4 and gpt-5.5 left this list when GPT-6 arrived; the GPT-5.6 line
# left on 2026-10-19, ahead of Terra's 2026-11-16 retirement. The retired ids
# keep a context profile below only because AI Platform still serves them.
# Copilot's auto routing (below) may answer with a model outside this list;
# that is Copilot's pick, not a member's, and it is admitted on its own
# metadata (see GitHubCopilotProvider.resolve_auto_model).
SUPPORTED_COPILOT_MODEL_IDS = (
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
)

# Copilot "auto" is not a model the chat API accepts but a routing mode, the
# one current VS Code uses: the runtime posts the turn's prompt and a tier to
# ``POST /auto``, Copilot answers with the model it chose (``selected_model``,
# carrying that model's metadata), a session token and the discount, and the
# chat request then sends the chosen id plus ``Copilot-Session-Token``. The
# tier is part of the model id so every layer that keys on the id (Portal
# picker, runtime profile, this canonicalization) needs no extra field:
# ``auto`` is the picker's Balance default, the two others are VS Code's
# selectable alternatives ("fast", the inline-chat tier, is not offered).
AUTO_MODEL_ID = "auto"
DEFAULT_AUTO_TIER = "balance"
COPILOT_AUTO_TIERS = {
    AUTO_MODEL_ID: DEFAULT_AUTO_TIER,
    "auto-efficiency": "efficiency",
    "auto-intelligence": "intelligence",
}
AUTO_MODEL_IDS = tuple(COPILOT_AUTO_TIERS)

# AI Platform (enterprise gateway) models. Must stay aligned with the Portal
# catalog (app/contracts/llm_catalog.py AI_PLATFORM_MODELS): the runtime coerces
# any model outside this tuple back to DEFAULT_AI_PLATFORM_MODEL, so a model the
# Portal offers but this list lacks is silently replaced by gpt-5.4.
DEFAULT_AI_PLATFORM_MODEL = "gpt-5.4"
AI_PLATFORM_MODEL_IDS = (
    "gpt-5.4",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
)


@dataclass(frozen=True)
class ModelContextProfile:
    """Deterministic context sizing metadata for a Copilot-hosted model."""

    provider_id: str
    model_id: str
    context_window_tokens: int
    default_reserve_tokens: int
    default_preserve_recent_tokens: int
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN

    def tokens_to_chars(self, tokens: int) -> int:
        """Convert token counts to deterministic approximate character counts."""

        return tokens_to_chars(tokens, chars_per_token=self.chars_per_token)


def resolve_model_context_profile(
    model: Any = None,
    *,
    provider_id: Any = DEFAULT_PROVIDER_ID,
) -> ModelContextProfile:
    """Resolve a model context profile with a conservative fallback.

    The catalog is keyed by model id alone: a context window is a property of
    the model, not of the gateway serving it. ``provider_id`` is accepted (and
    a ``provider/model`` prefix is stripped from ``model``) so callers can pass
    a qualified id, but it does not gate the lookup - ai_platform serves the
    same GPT-5.x line (see ``AI_PLATFORM_MODEL_IDS``) with the same 1M-class
    window as Copilot, and this profile is the default source of the native
    runtime's context budget, so falling back to the 64k profile purely
    because of a provider id would compact those sessions roughly seven times
    too early.
    """

    del provider_id  # accepted for call-site compatibility; see docstring
    _, requested_model = _split_model_id(model)
    model_id = requested_model or DEFAULT_MODEL_ID
    # What the Copilot API said about the model at run time (see
    # ``register_model_context_profile``) beats the hand-maintained row.
    learned = _LEARNED_PROFILES.get(model_id)
    if learned is not None:
        return learned
    return _COPILOT_PROFILES.get(
        model_id,
        replace(_CONSERVATIVE_FALLBACK_PROFILE, model_id=model_id),
    )


CHEAP_MODEL_IDS = {
    # Luna is the fast-and-cheap member of each line (see the catalogs above).
    "github_copilot": "gpt-6-luna",
    "ai_platform": "gpt-5.6-luna",
}


def cheap_model_for_family(family: str) -> str:
    """The cheapest catalog model of a provider family, for one-shot calls such as session titles.

    Falls back to the family's default model when the cheap id leaves the
    catalog, so a catalog change never breaks the callers.
    """

    if family == "ai_platform":
        cheap = CHEAP_MODEL_IDS["ai_platform"]
        return cheap if cheap in AI_PLATFORM_MODEL_IDS else DEFAULT_AI_PLATFORM_MODEL
    cheap = CHEAP_MODEL_IDS["github_copilot"]
    return cheap if cheap in SUPPORTED_COPILOT_MODEL_IDS else DEFAULT_MODEL_ID


def canonicalize_copilot_model_id(value: Any) -> str:
    """Return a supported canonical GitHub Copilot model id or raise."""

    text = _model_identifier(value)
    if text is None:
        raise ValueError(_unsupported_model_message(value))
    if "/" in text:
        provider, model = text.split("/", 1)
        if _normalize_identifier(provider) not in {
            DEFAULT_PROVIDER_ID,
            "github_copilot",
            "copilot",
        }:
            raise ValueError(_unsupported_model_message(value))
        text = model
    model_id = _normalize_model_identifier(text)
    if model_id in COPILOT_AUTO_TIERS:
        return model_id
    if model_id not in SUPPORTED_COPILOT_MODEL_IDS:
        raise ValueError(_unsupported_model_message(value))
    return model_id


def is_auto_model_id(value: Any) -> bool:
    """Whether ``value`` is an auto id (``auto``, a tier id, or either behind ``github-copilot/``)."""

    _, model_id = _split_model_id(value)
    return model_id in COPILOT_AUTO_TIERS


def auto_tier_for(value: Any) -> str | None:
    """The ``POST /auto`` tier an auto id asks for, or None for a concrete model."""

    _, model_id = _split_model_id(value)
    return COPILOT_AUTO_TIERS.get(model_id) if model_id else None


def static_model_context_profile(model: Any) -> ModelContextProfile | None:
    """The hand-maintained catalog row for ``model``, or None when it has none."""

    _, model_id = _split_model_id(model)
    return _COPILOT_PROFILES.get(model_id) if model_id else None


def copilot_model_context_profile(entry: Any) -> ModelContextProfile | None:
    """Build a profile from a Copilot API model entry, or None without usable limits.

    ``entry`` is a ``GET /models`` row or the ``selected_model`` of a ``POST
    /auto`` answer: ``{"id": ..., "capabilities": {"limits": {
    "max_context_window_tokens", "max_prompt_tokens", "max_output_tokens"}}}``.
    The output cap becomes the reserve; without one the gap between the
    window and the prompt cap does, and failing both a quarter of the window
    (at most 128k) keeps the reply from starving.
    """

    if not isinstance(entry, Mapping):
        return None
    model_id = _normalize_model_identifier(entry.get("id"))
    if not model_id:
        return None
    capabilities = entry.get("capabilities")
    limits = capabilities.get("limits") if isinstance(capabilities, Mapping) else None
    if not isinstance(limits, Mapping):
        return None
    window = _positive_int_or_none(limits.get("max_context_window_tokens"))
    if window is None:
        return None
    output = _positive_int_or_none(limits.get("max_output_tokens"))
    prompt = _positive_int_or_none(limits.get("max_prompt_tokens"))
    if output is not None and output < window:
        reserve = output
    elif prompt is not None and prompt < window:
        reserve = window - prompt
    else:
        reserve = min(128_000, window // 4)
    return _profile(
        model_id,
        context_window_tokens=window,
        default_reserve_tokens=reserve,
    )


def model_prompt_budget_tokens(profile: ModelContextProfile) -> int:
    """Tokens a prompt may use once the reserve and the safety margin are held back."""

    return max(1, default_max_context_tokens(profile) - profile.default_reserve_tokens)


def preferred_model_context_profile(
    *profiles: ModelContextProfile | None,
) -> ModelContextProfile | None:
    """The first profile given, skipping None: the caller lists its sources by trust.

    Copilot describes a model in more than one place; the ``GET /models``
    row is the gateway's own statement and comes first, the ``/auto`` answer
    embeds the same entry and comes next, this module's rows are the offline
    default. (Taking the smallest of them instead, as the first cut did, kept
    the headerless catalog's 400k alive against the gateway's 1M.)
    """

    for profile in profiles:
        if profile is not None:
            return profile
    return None


def register_model_context_profile(profile: ModelContextProfile) -> ModelContextProfile:
    """Remember a profile learned from the Copilot API; it then wins the lookup for its id."""

    _LEARNED_PROFILES[profile.model_id] = profile
    return profile


def clear_learned_model_context_profiles() -> None:
    """Forget every learned profile (tests)."""

    _LEARNED_PROFILES.clear()


def tokens_to_chars(
    tokens: int,
    *,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """Deterministically approximate a token budget as a character budget."""

    _validate_non_negative_int(tokens, "tokens")
    _validate_positive_int(chars_per_token, "chars_per_token")
    return int(tokens) * int(chars_per_token)


def context_safety_margin_tokens(profile: ModelContextProfile) -> int:
    """Return the token margin held back from a model's declared window.

    Mirrors the legacy safety-margin rule in ``src/runtime/progressive_context.py``
    (``min(configured_safety, int(context_window * 0.05))`` with a configured
    safety of 8000 tokens). Combined with ``default_reserve_tokens`` this
    reproduces the legacy prompt budget ``context_window - reserved - safety``;
    the legacy path additionally clamps by a configured ``max_prompt_tokens``,
    which has no native-runtime equivalent, and its window table
    (``src/config.py`` ``DEFAULT_MODEL_LIMITS``) disagrees with this one for
    the GPT-5.x line (400k/328k there against 1M here).
    """

    return min(
        DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS,
        profile.context_window_tokens // CONTEXT_SAFETY_MARGIN_WINDOW_DIVISOR,
    )


def default_max_context_tokens(profile: ModelContextProfile) -> int:
    """Return the catalog-derived prompt budget for a model, in tokens.

    The model's response headroom (``default_reserve_tokens``) is NOT subtracted
    here; callers subtract it separately via ``ContextBudget.reserve_chars``.
    """

    return max(1, profile.context_window_tokens - context_safety_margin_tokens(profile))


def is_catalog_model_context_profile(profile: ModelContextProfile) -> bool:
    """Report whether ``profile`` came from the catalog rather than the fallback.

    ``resolve_model_context_profile`` returns
    ``replace(_CONSERVATIVE_FALLBACK_PROFILE, model_id=model_id)`` on a miss, so
    ``profile.model_id`` alone cannot tell a real catalog entry from a guess -
    an uncatalogued model reports its own id next to a 64k window it never
    declared.

    Callers must not narrow an operator's explicit context budget on a profile
    this returns ``False`` for: a newly released or gateway-only model needs an
    override precisely because the conservative fallback is wrong about it, and
    clamping to 64k would defeat the only purpose the override has.
    """

    return profile in (
        _COPILOT_PROFILES.get(profile.model_id),
        _LEARNED_PROFILES.get(profile.model_id),
    )


def _profile(
    model_id: str,
    *,
    context_window_tokens: int,
    default_reserve_tokens: int,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
) -> ModelContextProfile:
    return ModelContextProfile(
        provider_id=DEFAULT_PROVIDER_ID,
        model_id=model_id,
        context_window_tokens=context_window_tokens,
        default_reserve_tokens=default_reserve_tokens,
        default_preserve_recent_tokens=_default_preserve_recent_tokens(
            context_window_tokens=context_window_tokens,
            reserve_tokens=default_reserve_tokens,
        ),
        chars_per_token=chars_per_token,
    )


def _default_preserve_recent_tokens(
    *,
    context_window_tokens: int,
    reserve_tokens: int,
) -> int:
    usable_tokens = max(0, context_window_tokens - reserve_tokens)
    return min(
        MAX_PRESERVE_RECENT_TOKENS,
        max(MIN_PRESERVE_RECENT_TOKENS, usable_tokens // 4),
    )


_COPILOT_PROFILES = {
    # Not Copilot models any more (see SUPPORTED_COPILOT_MODEL_IDS); kept for
    # AI Platform, which still serves them and resolves their budgets through
    # here.
    "gpt-5.4": _profile(
        "gpt-5.4",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-5.6-luna": _profile(
        "gpt-5.6-luna",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-5.6-sol": _profile(
        "gpt-5.6-sol",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-5.6-terra": _profile(
        "gpt-5.6-terra",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    # GPT-6 (released 2026-09; in Copilot since 2026-09-04 for Astra and
    # 2026-09-22 for Sol and Luna) and GPT-6.1 Sol (Copilot GA 2026-09-29).
    # Copilot's catalog (``GET /models`` with the API-version header, Business
    # plan, 2026-10-20) carries OpenAI's windows: 1,050,000 tokens for Astra
    # and 6.1 Sol, 1,000,000 for Sol and Luna, a 128,000-token output cap held
    # back as the reserve in every case. Its ``billing.token_prices`` adds two
    # pricing tiers: ``default`` up to a 272,000-token prompt and
    # ``long_context`` above it at twice the input price; the window is the
    # full one either way (the older, headerless schema reported 400,000).
    # At run time the provider takes the catalog row over these (see
    # ``preferred_model_context_profile``); the rows are the offline default.
    # Astra is the flagship reasoning model, Sol the balanced default for
    # interactive and agentic coding, Luna the fast and cheap one. Sol and
    # Luna also accept reasoning effort ``none``; Astra and 6.1 Sol do not,
    # and the runtime does not offer it for any model.
    "gpt-6-astra": _profile(
        "gpt-6-astra",
        context_window_tokens=1_050_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-6-luna": _profile(
        "gpt-6-luna",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-6-sol": _profile(
        "gpt-6-sol",
        context_window_tokens=1_000_000,
        default_reserve_tokens=128_000,
    ),
    "gpt-6.1-sol": _profile(
        "gpt-6.1-sol",
        context_window_tokens=1_050_000,
        default_reserve_tokens=128_000,
    ),
    # Copilot auto routes per turn and the runner takes the budget from the
    # model Copilot picked (RuntimeLoopRunner._model_context_profile); these
    # rows only cover a run that could not be routed, which sends
    # DEFAULT_MODEL_ID.
    **{
        auto_id: _profile(
            auto_id,
            context_window_tokens=1_000_000,
            default_reserve_tokens=128_000,
        )
        for auto_id in AUTO_MODEL_IDS
    },
}
# Profiles learned from the Copilot API at run time, keyed by model id (see
# ``register_model_context_profile``).
_LEARNED_PROFILES: dict[str, ModelContextProfile] = {}

_CONSERVATIVE_FALLBACK_PROFILE = _profile(
    "unknown",
    context_window_tokens=64_000,
    default_reserve_tokens=4_000,
)


def _split_model_id(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    if not text:
        return None, None
    if "/" not in text:
        return None, _normalize_model_identifier(text)
    provider, model = text.split("/", 1)
    return _normalize_identifier(provider), _normalize_model_identifier(model)


def _normalize_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def _model_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def _normalize_model_identifier(value: Any) -> str | None:
    text = _model_identifier(value)
    if text is None:
        return None
    return "-".join(text.split())


def _unsupported_model_message(value: Any) -> str:
    supported = ", ".join((*SUPPORTED_COPILOT_MODEL_IDS, *AUTO_MODEL_IDS))
    return "unsupported GitHub Copilot model {0!r}; supported models: {1}".format(
        value,
        supported,
    )


def _positive_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or value < 1:
        return None
    return value


def _validate_non_negative_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_positive_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


__all__ = [
    "AUTO_MODEL_ID",
    "AUTO_MODEL_IDS",
    "CONTEXT_SAFETY_MARGIN_WINDOW_DIVISOR",
    "COPILOT_AUTO_TIERS",
    "DEFAULT_AUTO_TIER",
    "DEFAULT_CHARS_PER_TOKEN",
    "DEFAULT_CONTEXT_SAFETY_MARGIN_TOKENS",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PROVIDER_ID",
    "MAX_PRESERVE_RECENT_TOKENS",
    "MIN_PRESERVE_RECENT_TOKENS",
    "ModelContextProfile",
    "SUPPORTED_COPILOT_MODEL_IDS",
    "auto_tier_for",
    "canonicalize_copilot_model_id",
    "cheap_model_for_family",
    "clear_learned_model_context_profiles",
    "context_safety_margin_tokens",
    "copilot_model_context_profile",
    "default_max_context_tokens",
    "is_auto_model_id",
    "is_catalog_model_context_profile",
    "model_prompt_budget_tokens",
    "register_model_context_profile",
    "resolve_model_context_profile",
    "preferred_model_context_profile",
    "static_model_context_profile",
    "tokens_to_chars",
]
