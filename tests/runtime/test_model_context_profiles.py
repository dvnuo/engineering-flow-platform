from __future__ import annotations

import pytest

from efp_runtime.llm.models import (
    DEFAULT_PROVIDER_ID,
    ModelContextProfile,
    resolve_model_context_profile,
    tokens_to_chars,
)


@pytest.mark.parametrize(
    ("model", "expected_model"),
    [
        ("github-copilot/gpt-5", "gpt-5"),
        ("github-copilot/gpt-5-mini", "gpt-5-mini"),
        ("gpt-5", "gpt-5"),
        ("gpt-5-mini", "gpt-5-mini"),
    ],
)
def test_github_copilot_profile_resolution(model: str, expected_model: str):
    profile = resolve_model_context_profile(model)

    assert isinstance(profile, ModelContextProfile)
    assert profile.provider_id == DEFAULT_PROVIDER_ID
    assert profile.model_id == expected_model
    assert profile.context_window_tokens == 128_000
    assert profile.default_reserve_tokens == 8_000
    assert profile.default_preserve_recent_tokens == 8_000
    assert profile.tokens_to_chars(100) == 400


def test_unknown_model_falls_back_to_conservative_copilot_profile():
    profile = resolve_model_context_profile("some-new-model")

    assert profile.provider_id == DEFAULT_PROVIDER_ID
    assert profile.model_id == "some-new-model"
    assert profile.context_window_tokens == 64_000
    assert profile.default_reserve_tokens == 4_000
    assert profile.default_preserve_recent_tokens == 8_000
    assert profile.chars_per_token == 4


def test_non_copilot_qualified_model_still_uses_copilot_fallback():
    profile = resolve_model_context_profile("other-provider/custom-model")

    assert profile.provider_id == DEFAULT_PROVIDER_ID
    assert profile.model_id == "custom-model"
    assert profile.context_window_tokens == 64_000


def test_tokens_to_chars_is_deterministic_and_validated():
    assert tokens_to_chars(12, chars_per_token=5) == 60
    with pytest.raises(ValueError):
        tokens_to_chars(-1)
    with pytest.raises(ValueError):
        tokens_to_chars(1, chars_per_token=0)
