"""GET /api/models: Copilot's catalog projected, admitted into the allowlist, the static fallback."""

from __future__ import annotations

import pytest

from src.efp_runtime.llm import models as llm_models
from src.efp_runtime.llm.pricing import clear_catalog_pricing, pricing_for_model
from src.gateway import copilot_models, runtime_chat


def _row(model_id, **overrides):
    row = {
        "id": model_id,
        "name": model_id.upper(),
        "vendor": "OpenAI",
        "version": f"{model_id}-2026-09-29",
        "preview": False,
        "model_picker_enabled": True,
        "model_picker_category": "versatile",
        "policy": {"state": "enabled", "terms": "Enabled by the organization."},
        "supported_endpoints": ["/chat/completions", "/responses"],
        "capabilities": {
            "type": "chat",
            "family": model_id,
            "limits": {
                "max_context_window_tokens": 1_050_000,
                "max_prompt_tokens": 922_000,
                "max_output_tokens": 128_000,
            },
            "supports": {
                "tool_calls": True,
                "parallel_tool_calls": True,
                "vision": True,
                "streaming": True,
                "structured_outputs": True,
                "reasoning_effort": ["low", "medium", "high", "xhigh", "max"],
            },
        },
        "billing": {
            "is_premium": True,
            "multiplier": 1,
            "token_prices": {
                "default": {"max_prompt_tokens": 272_000, "input_price": 200, "output_price": 1000, "cached_input_price": 10},
                "long_context": {"max_prompt_tokens": 922_000, "input_price": 400, "output_price": 1500, "cached_input_price": 20},
            },
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(row.get(key), dict):
            row[key] = {**row[key], **value}
        else:
            row[key] = value
    return row


CATALOG = {
    "gpt-6.1-sol": _row("gpt-6.1-sol"),
    "gpt-6.2-nova": _row(
        "gpt-6.2-nova",
        capabilities={
            "type": "chat",
            "limits": {"max_context_window_tokens": 1_200_000, "max_prompt_tokens": 1_000_000, "max_output_tokens": 128_000},
            "supports": {"tool_calls": True, "vision": False, "reasoning_effort": ["medium", "high"]},
        },
        billing={"token_prices": {"default": {"input_price": 300, "output_price": 1200}}},
    ),
    "claude-sonnet-5.5": _row("claude-sonnet-5.5", vendor="Anthropic", supported_endpoints=["/chat/completions"]),
    "text-embedding-3-small": _row("text-embedding-3-small", capabilities={"type": "embeddings", "limits": {}, "supports": {}}),
    "gpt-6-luna": _row("gpt-6-luna", policy={"state": "disabled"}),
    "gpt-5.3-codex": _row("gpt-5.3-codex", model_picker_enabled=False),
    "mai-code-1.1-flash": _row("mai-code-1.1-flash", vendor="Microsoft", capabilities={"supports": {"tool_calls": False}}),
}


@pytest.fixture(autouse=True)
def _clean():
    def reset():
        copilot_models.clear_models_cache()
        llm_models.clear_catalog_model_ids()
        llm_models.clear_learned_model_context_profiles()
        clear_catalog_pricing()

    reset()
    yield
    reset()


def test_catalog_rows_are_projected_and_filtered():
    payload = copilot_models.models_payload_from_catalog(CATALOG, fetched_at="t")

    assert payload["available"] is True
    assert payload["source"] == "catalog"
    assert payload["fetched_at"] == "t"
    assert payload["provider"] == "github-copilot"
    assert payload["selectable"] == ["gpt-6.1-sol", "gpt-6.2-nova"]
    assert [entry["id"] for entry in payload["auto"]] == ["auto", "auto-efficiency", "auto-intelligence"]
    assert payload["auto"][0] == {"id": "auto", "label": "Auto", "tier": "balance"}

    by_id = {entry["id"]: entry for entry in payload["models"]}
    sol = by_id["gpt-6.1-sol"]
    assert sol["selectable"] is True
    assert sol["reasons"] == []
    assert sol["known"] is True
    assert sol["name"] == "GPT-6.1-SOL"
    assert sol["vendor"] == "OpenAI"
    assert sol["category"] == "versatile"
    assert sol["policy"] == "enabled"
    assert sol["picker_enabled"] is True
    assert sol["limits"] == {"context_window_tokens": 1_050_000, "prompt_tokens": 922_000, "output_tokens": 128_000}
    assert sol["supports"]["tool_calls"] is True
    assert sol["supports"]["vision"] is True
    assert sol["supports"]["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert sol["endpoints"] == ["/chat/completions", "/responses"]
    assert sol["pricing"]["default"] == {"prompt_cap_tokens": 272_000, "input": 200.0, "output": 1000.0, "cached_input": 10.0, "cache_write": None}
    assert sol["pricing"]["long_context"]["input"] == 400.0
    assert sol["billing_threshold_tokens"] == 272_000

    nova = by_id["gpt-6.2-nova"]
    assert nova["selectable"] is True
    assert nova["known"] is False
    assert nova["billing_threshold_tokens"] is None
    assert nova["supports"]["reasoning_efforts"] == ["medium", "high"]

    assert by_id["claude-sonnet-5.5"]["selectable"] is False
    assert by_id["claude-sonnet-5.5"]["reasons"] == ["not served on /responses"]
    assert by_id["text-embedding-3-small"]["reasons"] == ["not a chat model (embeddings)"]
    assert by_id["gpt-6-luna"]["reasons"] == ["policy disabled"]
    assert by_id["gpt-5.3-codex"]["reasons"] == ["hidden from the picker"]
    assert by_id["mai-code-1.1-flash"]["reasons"] == ["no tool calls"]


def test_selectable_catalog_models_join_the_allowlist_with_their_limits_and_prices():
    with pytest.raises(ValueError):
        llm_models.canonicalize_copilot_model_id("gpt-6.2-nova")

    copilot_models.models_payload_from_catalog(CATALOG)

    assert llm_models.canonicalize_copilot_model_id("gpt-6.2-nova") == "gpt-6.2-nova"
    assert llm_models.canonicalize_copilot_model_id("github-copilot/GPT-6.2-Nova") == "gpt-6.2-nova"
    assert llm_models.is_supported_copilot_model_id("gpt-6.2-nova") is True
    assert llm_models.catalog_model_ids() == ("gpt-6.1-sol", "gpt-6.2-nova")
    # Rows the runtime cannot drive stay out; the static list still stands.
    assert llm_models.is_supported_copilot_model_id("claude-sonnet-5.5") is False
    assert llm_models.is_supported_copilot_model_id("gpt-6-luna") is True
    assert llm_models.is_supported_copilot_model_id("auto") is True

    profile = llm_models.resolve_model_context_profile("gpt-6.2-nova")
    assert profile.context_window_tokens == 1_200_000
    assert profile.default_reserve_tokens == 128_000
    assert pricing_for_model("gpt-6.2-nova") is not None
    assert pricing_for_model("gpt-6.2-nova").default.input_usd_per_1m == pytest.approx(3.0)


def test_static_payload_lists_the_hand_maintained_models_with_prices():
    payload = copilot_models.static_models_payload(error="no Copilot credential configured")

    assert payload["source"] == "static"
    assert payload["error"] == "no Copilot credential configured"
    assert payload["selectable"] == list(llm_models.SUPPORTED_COPILOT_MODEL_IDS)
    assert [entry["id"] for entry in payload["auto"]] == ["auto", "auto-efficiency", "auto-intelligence"]
    sol = next(entry for entry in payload["models"] if entry["id"] == "gpt-6-sol")
    assert sol["selectable"] is True
    assert sol["known"] is True
    assert sol["limits"]["context_window_tokens"] > sol["limits"]["prompt_tokens"] > 0
    assert sol["limits"]["output_tokens"] == 128_000
    assert sol["pricing"]["default"]["input"] == 200
    assert sol["pricing"]["default"]["output"] == 1000
    assert sol["pricing"]["long_context"]["input"] == 400
    assert sol["billing_threshold_tokens"] == 272_000
    assert sol["endpoints"] == ["/responses"]

    ai_platform = copilot_models.static_models_payload(provider="ai_platform")
    assert ai_platform["provider"] == "ai_platform"
    assert ai_platform["auto"] == []
    assert "gpt-5.4" in ai_platform["selectable"]
    assert ai_platform["default_model"] == "gpt-5.4"


def test_runtime_lookup_caches_per_credential_and_falls_back_to_the_static_list(monkeypatch):
    monkeypatch.setattr(copilot_models, "_configured_provider", lambda: "github_copilot")
    monkeypatch.setattr(copilot_models, "_runtime_credential", lambda: "ghu_abc")
    calls: list[bool] = []

    def fake_fetch(*, force):
        calls.append(force)
        return CATALOG

    monkeypatch.setattr(copilot_models, "_fetch_catalog", fake_fetch)

    first = copilot_models.copilot_models_for_runtime(now=0.0)
    assert first["source"] == "catalog"
    assert first["cached"] is False
    second = copilot_models.copilot_models_for_runtime(now=10.0)
    assert second["cached"] is True
    assert calls == [False]
    copilot_models.copilot_models_for_runtime(now=10.0, force=True)
    assert calls == [False, True]
    assert copilot_models.cached_models_payload()["source"] == "catalog"

    def failing(*, force):
        raise RuntimeError("GitHub Copilot models request failed with HTTP 503")

    monkeypatch.setattr(copilot_models, "_fetch_catalog", failing)
    fallen = copilot_models.copilot_models_for_runtime(now=10.0, force=True)
    assert fallen["source"] == "static"
    assert "503" in fallen["error"]
    assert fallen["selectable"] == list(llm_models.SUPPORTED_COPILOT_MODEL_IDS)
    # A failure is remembered briefly, then retried.
    assert copilot_models.copilot_models_for_runtime(now=20.0)["cached"] is True
    retried = copilot_models.copilot_models_for_runtime(now=20.0 + copilot_models.FAILURE_TTL_SECONDS + 1)
    assert retried["cached"] is False
    assert retried["source"] == "static"


def test_without_a_credential_the_static_list_answers(monkeypatch):
    monkeypatch.setattr(copilot_models, "_configured_provider", lambda: "")
    monkeypatch.setattr(copilot_models, "_runtime_credential", lambda: None)
    payload = copilot_models.copilot_models_for_runtime()
    assert payload["source"] == "static"
    assert "credential" in payload["error"]

    monkeypatch.setattr(copilot_models, "_configured_provider", lambda: "ai_platform")
    payload = copilot_models.copilot_models_for_runtime()
    assert payload["provider"] == "ai_platform"
    assert payload["source"] == "static"


def test_admit_model_from_catalog_accepts_only_selectable_rows(monkeypatch):
    monkeypatch.setattr(copilot_models, "_configured_provider", lambda: "github_copilot")
    monkeypatch.setattr(copilot_models, "_runtime_credential", lambda: "ghu_abc")
    monkeypatch.setattr(copilot_models, "_fetch_catalog", lambda *, force: CATALOG)

    assert copilot_models.admit_model_from_catalog("github-copilot/gpt-6.2-nova") == "gpt-6.2-nova"
    assert llm_models.canonicalize_copilot_model_id("gpt-6.2-nova") == "gpt-6.2-nova"
    assert copilot_models.admit_model_from_catalog("claude-sonnet-5.5") is None
    assert copilot_models.admit_model_from_catalog("auto") is None
    assert copilot_models.admit_model_from_catalog("") is None
    assert copilot_models.admit_model_from_catalog("gpt-6.1-sol", fetch=False) == "gpt-6.1-sol"


def test_the_gateway_resolves_a_catalog_model_and_still_rejects_unknown_ones(monkeypatch):
    monkeypatch.setattr(
        runtime_chat,
        "_admit_model_from_copilot_catalog",
        lambda text: "gpt-6.2-nova" if text == "gpt-6.2-nova" else None,
    )
    assert runtime_chat._resolve_model("gpt-6.2-nova") == "gpt-6.2-nova"
    assert runtime_chat._resolve_model("gpt-6-sol") == "gpt-6-sol"
    with pytest.raises(runtime_chat.RuntimeChatError) as excinfo:
        runtime_chat._resolve_model("gpt-9-bogus")
    assert excinfo.value.error_type == "invalid_model"
