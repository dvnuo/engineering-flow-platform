from __future__ import annotations

import pytest

from efp_runtime.llm.models import (
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    ModelContextProfile,
    SUPPORTED_COPILOT_MODEL_IDS,
    canonicalize_copilot_model_id,
    is_catalog_model_context_profile,
    resolve_model_context_profile,
    tokens_to_chars,
)


@pytest.mark.parametrize(
    ("model", "expected_model"),
    [
        ("github-copilot/gpt-5.4", "gpt-5.4"),
        ("github-copilot/gpt-5.5", "gpt-5.5"),
        ("github-copilot/gpt-5.6-luna", "gpt-5.6-luna"),
        ("github-copilot/gpt-5.6-sol", "gpt-5.6-sol"),
        ("github-copilot/gpt-5.6-terra", "gpt-5.6-terra"),
        ("gpt-5.4", "gpt-5.4"),
        ("gpt-5.5", "gpt-5.5"),
        ("gpt-5.6 luna", "gpt-5.6-luna"),
        ("gpt-5.6 sol", "gpt-5.6-sol"),
        ("gpt-5.6 terra", "gpt-5.6-terra"),
    ],
)
def test_github_copilot_profile_resolution(model: str, expected_model: str):
    profile = resolve_model_context_profile(model)

    assert isinstance(profile, ModelContextProfile)
    assert profile.provider_id == DEFAULT_PROVIDER_ID
    assert profile.model_id == expected_model
    if expected_model == "gpt-5.6-luna":
        assert profile.context_window_tokens == 328_000
        assert profile.default_reserve_tokens == 128_000
    else:
        assert profile.context_window_tokens == 400_000
        assert profile.default_reserve_tokens == 128_000
    assert profile.default_preserve_recent_tokens == 8_000
    assert profile.tokens_to_chars(100) == 400


def test_default_and_supported_model_list_are_copilot_responses_models():
    assert DEFAULT_MODEL_ID == "gpt-5.6-terra"
    assert SUPPORTED_COPILOT_MODEL_IDS == (
        "gpt-5.4",
        "gpt-5.5",
        "gpt-5.6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    )


def test_canonicalize_copilot_model_id_rejects_unsupported_models():
    assert canonicalize_copilot_model_id("gpt-5.6 terra") == "gpt-5.6-terra"
    with pytest.raises(ValueError, match="unsupported GitHub Copilot model"):
        canonicalize_copilot_model_id("gpt-5")


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


@pytest.mark.parametrize(
    "model",
    [*SUPPORTED_COPILOT_MODEL_IDS, "github-copilot/gpt-5.6-sol", "gpt-5.6 sol"],
)
def test_catalog_hit_is_distinguishable_from_the_conservative_fallback(model: str):
    assert is_catalog_model_context_profile(resolve_model_context_profile(model)) is True


@pytest.mark.parametrize("model", ["some-new-model", "other-provider/custom-model"])
def test_fallback_profile_is_not_reported_as_a_catalog_hit(model: str):
    """``model_id`` alone cannot tell the two apart, and callers depend on this.

    ``resolve_model_context_profile`` stamps the requested id onto a copy of the
    conservative fallback, so a miss reports its own id next to a 64k window it
    never declared. The context-budget clamp must not narrow an operator's
    override on one of these.
    """

    profile = resolve_model_context_profile(model)

    assert profile.context_window_tokens == 64_000
    assert is_catalog_model_context_profile(profile) is False


def test_tokens_to_chars_is_deterministic_and_validated():
    assert tokens_to_chars(12, chars_per_token=5) == 60
    with pytest.raises(ValueError):
        tokens_to_chars(-1)
    with pytest.raises(ValueError):
        tokens_to_chars(1, chars_per_token=0)
