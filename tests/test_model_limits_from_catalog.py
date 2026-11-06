"""The legacy context budget reads the limits Copilot's catalog reported for a model.

``src.config.resolve_model_limits`` and ``src.agents.compaction.resolve_context_window_tokens``
budget the progressive-context pass that runs after a task, skill, subagent
or event turn (``src/runtime/progressive_context.py``) from hand-written
tables. A model only the catalog lists, such as MAI Code 1 Flash, is in
neither: its window came out as 4k and every turn compacted. The runtime
learns each selectable row's limits when it serves ``GET /api/models`` or
warms a concrete pick (``register_model_context_profile``); these tests pin
that the legacy path reads them.
"""

from __future__ import annotations

import pytest

from src import config as config_module
from src.agents.compaction import resolve_context_window_tokens
from src.config import learned_model_limits as config_learned_model_limits
from src.config import resolve_model_limits
from src.efp_runtime.llm.models import (
    clear_learned_model_context_profiles,
    copilot_model_context_profile,
    learned_model_limits,
    register_model_context_profile,
    static_model_context_profile,
)
from src.runtime.progressive_context import resolve_prompt_budget

MAI = "mai-code-1-flash-2026-06-02"


def _row(model_id, *, window, prompt=None, output=None):
    """A ``GET /models`` row reduced to the limits the budget reads."""

    limits = {"max_context_window_tokens": window}
    if prompt is not None:
        limits["max_prompt_tokens"] = prompt
    if output is not None:
        limits["max_output_tokens"] = output
    return {"id": model_id, "capabilities": {"limits": limits}}


def _learn(row):
    profile = copilot_model_context_profile(row)
    assert profile is not None
    return register_model_context_profile(profile)


def _llm_config(monkeypatch, **llm):
    """Point ``config.llm`` at a copy without operator limits, plus ``llm``."""

    base = dict(config_module.config._config)
    current = dict(base.get("llm") or {})
    current.pop("model_limits", None)
    current.pop("context_budget", None)
    current.update(llm)
    base["llm"] = current
    monkeypatch.setattr(config_module.config, "_config", base)


@pytest.fixture(autouse=True)
def _forget_learned_profiles():
    clear_learned_model_context_profiles()
    yield
    clear_learned_model_context_profiles()


def test_catalog_profile_keeps_the_caps_the_row_lists():
    profile = copilot_model_context_profile(_row(MAI, window=256_000, prompt=128_000, output=64_000))

    assert profile.context_window_tokens == 256_000
    assert profile.default_reserve_tokens == 64_000
    assert profile.max_prompt_tokens == 128_000
    assert profile.max_output_tokens == 64_000
    # A hand-maintained row carries only the reserve.
    assert static_model_context_profile("gpt-6-sol").max_prompt_tokens is None
    assert static_model_context_profile("gpt-6-sol").max_output_tokens is None


def test_learned_model_limits_follow_the_row_and_derive_missing_caps():
    assert learned_model_limits(MAI) is None
    assert learned_model_limits(None) is None
    # Only a learned profile answers, never the hand-maintained rows.
    assert learned_model_limits("gpt-6-sol") is None

    _learn(_row(MAI, window=256_000, prompt=128_000, output=64_000))
    assert learned_model_limits(MAI) == {
        "max_context_window_tokens": 256_000,
        "max_prompt_tokens": 128_000,
        "max_output_tokens": 64_000,
    }
    assert learned_model_limits("github-copilot/" + MAI) == learned_model_limits(MAI)

    # Without an output cap the reserve is the window less the prompt cap and
    # stands in for it; without either, a quarter of the window does.
    _learn(_row("x-prompt-only", window=300_000, prompt=250_000))
    assert learned_model_limits("x-prompt-only") == {
        "max_context_window_tokens": 300_000,
        "max_prompt_tokens": 250_000,
        "max_output_tokens": 50_000,
    }
    _learn(_row("x-window-only", window=100_000))
    assert learned_model_limits("x-window-only") == {
        "max_context_window_tokens": 100_000,
        "max_prompt_tokens": 75_000,
        "max_output_tokens": 25_000,
    }
    # A prompt cap above the window is held to the window.
    _learn(_row("x-wide-prompt", window=100_000, prompt=120_000, output=10_000))
    assert learned_model_limits("x-wide-prompt")["max_prompt_tokens"] == 100_000


def test_resolve_model_limits_reads_the_learned_row_before_the_table(monkeypatch):
    _llm_config(monkeypatch, max_tokens=64_000)
    assert resolve_model_limits("gpt-6-sol") == config_module.DEFAULT_MODEL_LIMITS["gpt-6-sol"]

    _learn(_row("gpt-6-sol", window=1_200_000, prompt=1_000_000, output=96_000))
    learned = {"max_context_window_tokens": 1_200_000, "max_prompt_tokens": 1_000_000, "max_output_tokens": 96_000}
    assert resolve_model_limits("gpt-6-sol") == learned
    assert resolve_model_limits("github-copilot/gpt-6-sol") == learned
    assert config_learned_model_limits("github-copilot/gpt-6-sol") == learned
    # The other rows of the table are untouched.
    assert resolve_model_limits("gpt-6-luna") == config_module.DEFAULT_MODEL_LIMITS["gpt-6-luna"]


def test_resolve_model_limits_keeps_the_operator_row_above_the_catalog(monkeypatch):
    operator = {"max_context_window_tokens": 50_000, "max_prompt_tokens": 40_000, "max_output_tokens": 8_000}
    _llm_config(monkeypatch, max_tokens=64_000, model_limits={MAI: operator})
    _learn(_row(MAI, window=256_000, prompt=128_000, output=64_000))

    assert resolve_model_limits(MAI) == operator


def test_resolve_model_limits_without_a_learned_row_is_unchanged(monkeypatch):
    _llm_config(monkeypatch, max_tokens=64_000)

    assert config_learned_model_limits(MAI) is None
    assert config_learned_model_limits(None) is None
    assert resolve_model_limits(MAI) == {
        "max_context_window_tokens": 200_000,
        "max_prompt_tokens": 128_000,
        "max_output_tokens": 64_000,
    }


def test_configured_default_model_reads_its_learned_row(monkeypatch):
    _llm_config(monkeypatch, max_tokens=64_000, model=MAI)
    _learn(_row(MAI, window=256_000, prompt=128_000, output=64_000))

    assert resolve_model_limits(None)["max_context_window_tokens"] == 256_000
    assert resolve_context_window_tokens(None) == 256_000


def test_resolve_context_window_tokens_reads_the_learned_window():
    assert resolve_context_window_tokens(MAI) == 4096
    assert resolve_context_window_tokens("gpt-6-luna") == 1_000_000

    _learn(_row(MAI, window=256_000, prompt=128_000, output=64_000))
    _learn(_row("gpt-6-luna", window=400_000, prompt=272_000, output=128_000))

    assert resolve_context_window_tokens(MAI) == 256_000
    assert resolve_context_window_tokens("github-copilot/" + MAI) == 256_000
    assert resolve_context_window_tokens("gpt-6-luna") == 400_000
    assert resolve_context_window_tokens("unsupported-model") == 4096


def test_resolve_prompt_budget_budgets_a_catalog_only_model_to_its_row(monkeypatch):
    _llm_config(monkeypatch, max_tokens=64_000)

    before = resolve_prompt_budget(stage="post_turn", model=MAI)
    assert before["context_window_tokens"] == 4096
    assert before["prompt_budget_tokens"] == 4000

    _learn(_row(MAI, window=256_000, prompt=128_000, output=64_000))

    after = resolve_prompt_budget(stage="post_turn", model=MAI)
    assert after["context_window_tokens"] == 256_000
    assert after["max_prompt_tokens"] == 128_000
    assert after["max_output_tokens"] == 64_000
    assert after["reserved_output_tokens"] == 64_000
    assert after["safety_margin_tokens"] == 8_000
    assert after["prompt_budget_tokens"] == 128_000
