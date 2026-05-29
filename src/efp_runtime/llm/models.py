"""Copilot-first model context profiles for Runtime v2."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


DEFAULT_PROVIDER_ID = "github-copilot"
DEFAULT_MODEL_ID = "gpt-5-mini"
DEFAULT_CHARS_PER_TOKEN = 4
MIN_PRESERVE_RECENT_TOKENS = 2_000
MAX_PRESERVE_RECENT_TOKENS = 8_000


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
    """Resolve a Copilot model profile with a conservative Copilot fallback."""

    requested_provider, requested_model = _split_model_id(model)
    fallback_provider = _normalize_identifier(provider_id) or DEFAULT_PROVIDER_ID
    provider = requested_provider or fallback_provider
    model_id = requested_model or DEFAULT_MODEL_ID
    if provider != DEFAULT_PROVIDER_ID:
        return replace(_CONSERVATIVE_FALLBACK_PROFILE, model_id=model_id)
    return _COPILOT_PROFILES.get(
        model_id,
        replace(_CONSERVATIVE_FALLBACK_PROFILE, model_id=model_id),
    )


def tokens_to_chars(
    tokens: int,
    *,
    chars_per_token: int = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """Deterministically approximate a token budget as a character budget."""

    _validate_non_negative_int(tokens, "tokens")
    _validate_positive_int(chars_per_token, "chars_per_token")
    return int(tokens) * int(chars_per_token)


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
    "gpt-5": _profile(
        "gpt-5",
        context_window_tokens=128_000,
        default_reserve_tokens=8_000,
    ),
    "gpt-5-mini": _profile(
        "gpt-5-mini",
        context_window_tokens=128_000,
        default_reserve_tokens=8_000,
    ),
}

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
        return None, _normalize_identifier(text)
    provider, model = text.split("/", 1)
    return _normalize_identifier(provider), _normalize_identifier(model)


def _normalize_identifier(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    return text or None


def _validate_non_negative_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_positive_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")


__all__ = [
    "DEFAULT_CHARS_PER_TOKEN",
    "DEFAULT_MODEL_ID",
    "DEFAULT_PROVIDER_ID",
    "MAX_PRESERVE_RECENT_TOKENS",
    "MIN_PRESERVE_RECENT_TOKENS",
    "ModelContextProfile",
    "resolve_model_context_profile",
    "tokens_to_chars",
]
