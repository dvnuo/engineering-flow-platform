"""Copilot ``auto`` is a routing mode, not a model id.

The runtime does what current VS Code does behind its Auto picker: each user
turn posts the prompt and a tier to ``POST /auto``, Copilot answers with the
model it picked (``selected_model``, with that model's metadata), a session
token and the discount, and the chat request sends the picked id with the
``Copilot-Session-Token`` header. The pick is honoured when the runtime can
drive it; anything else degrades to the default model without a session.
"""

from __future__ import annotations

import io
import json
from urllib import error as urllib_error

import pytest

import efp_runtime.llm.models as models_module
import efp_runtime.llm.provider as provider_module
from efp_runtime.llm.models import (
    AUTO_MODEL_ID,
    AUTO_MODEL_IDS,
    COPILOT_AUTO_TIERS,
    DEFAULT_MODEL_ID,
    SUPPORTED_COPILOT_MODEL_IDS,
    auto_tier_for,
    canonicalize_copilot_model_id,
    clear_learned_model_context_profiles,
    copilot_model_context_profile,
    is_auto_model_id,
    is_catalog_model_context_profile,
    model_prompt_budget_tokens,
    preferred_model_context_profile,
    register_model_context_profile,
    resolve_model_context_profile,
    static_model_context_profile,
)
from efp_runtime.llm.provider import (
    COPILOT_AUTO_API_VERSION,
    COPILOT_AUTO_ROUTE_TIMEOUT_SECONDS,
    COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS,
    CopilotAutoDecision,
    GitHubCopilotHTTPTransport,
    GitHubCopilotProvider,
    ProviderAutoSessionError,
    RecordingTransport,
)
from efp_runtime.llm.request import ProviderRequest, RequestMessage, RequestMessagePart
from efp_runtime.loop import LoopStatus, RuntimeLoopRunner, RuntimeRequest
from efp_runtime.session.store import InMemorySessionStore
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime

FAR_FUTURE = 4_102_444_800


def _entry(model_id, *, window, prompt, output, efforts=None, endpoints=("/responses",), tool_calls=True, **extra):
    """A Copilot model entry as ``GET /models`` and ``selected_model`` describe it."""

    entry = {
        "id": model_id,
        "name": model_id,
        "vendor": "OpenAI",
        "capabilities": {
            "family": model_id,
            "tokenizer": "o200k_base",
            "limits": {
                "max_context_window_tokens": window,
                "max_prompt_tokens": prompt,
                "max_output_tokens": output,
            },
            "supports": {"tool_calls": tool_calls, "streaming": True},
        },
        "supported_endpoints": list(endpoints),
    }
    if efforts is not None:
        entry["capabilities"]["supports"]["reasoning_effort"] = list(efforts)
    entry.update(extra)
    return entry


# What the 2026-10-20 probes saw on a Business seat: /auto and GET /models
# (with the API-version header) both describe Luna with OpenAI's 1M window;
# the headerless catalog of the morning had reported 400k, which is what the
# precedence test below feeds in as the catalog row.
LUNA_AUTO = _entry("gpt-6-luna", window=1_000_000, prompt=872_000, output=128_000,
                   efforts=("none", "low", "medium", "high", "xhigh", "max"))
LUNA_CATALOG = _entry("gpt-6-luna", window=1_000_000, prompt=872_000, output=128_000,
                      efforts=("none", "low", "medium", "high", "xhigh", "max"),
                      billing={"token_prices": {"default": {"max_prompt_tokens": 272_000}, "long_context": {"max_prompt_tokens": 872_000}}})
LUNA_OLD_SCHEMA = _entry("gpt-6-luna", window=400_000, prompt=272_000, output=128_000,
                         efforts=("none", "low", "medium", "high", "xhigh", "max"))
SOL61_AUTO = _entry("gpt-6.1-sol", window=1_050_000, prompt=922_000, output=128_000,
                    efforts=("low", "medium", "high", "xhigh", "max"))
MAI = _entry("mai-code-1.1-flash", window=256_000, prompt=128_000, output=128_000,
             efforts=("low", "medium", "high"), vendor="Microsoft")


@pytest.fixture(autouse=True)
def _forget_learned_profiles():
    clear_learned_model_context_profiles()
    yield
    clear_learned_model_context_profiles()


def _request(metadata=None, *, prompt="Hello", session_id="session-auto") -> RuntimeRequest:
    return RuntimeRequest(
        session_id=session_id,
        messages=[],
        iteration=1,
        max_iterations=2,
        provider_request=ProviderRequest(
            messages=[RequestMessage(role="user", parts=[RequestMessagePart(type="text", text=prompt)])],
        ),
        metadata=dict(metadata or {}),
    )


def _responses_answer(text: str) -> dict:
    return {
        "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3},
    }


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")


class _RoutingTransport(RecordingTransport):
    """RecordingTransport that also answers ``POST /auto`` and ``GET /models``."""

    def __init__(self, responses, *, selected=LUNA_AUTO, catalog=(), error=None, discounts=None, expires_at=FAR_FUTURE):
        super().__init__(responses)
        self.selected = selected
        self.catalog = {entry["id"]: entry for entry in catalog}
        self.error = error
        self.discounts = {selected["id"]: 0.1} if discounts is None else dict(discounts)
        self.expires_at = expires_at
        self.route_calls: list[tuple[str, str | None, bool]] = []
        self.session_token = None

    def auto_route(self, prompt, *, tier=None, has_image=False) -> CopilotAutoDecision:
        self.route_calls.append((prompt, tier, has_image))
        if self.error is not None:
            raise self.error
        return CopilotAutoDecision(
            model=self.selected["id"],
            session_token="session-{0}".format(len(self.route_calls)),
            expires_at=self.expires_at,
            tier=tier,
            metadata=dict(self.selected),
            discounted_costs=dict(self.discounts),
        )

    def models_catalog(self, *, force=False):
        return dict(self.catalog)

    def set_auto_session_token(self, token):
        self.session_token = token


# --- catalog -----------------------------------------------------------------


def test_auto_ids_are_accepted_as_copilot_model_ids_but_are_not_concrete_models():
    assert AUTO_MODEL_IDS == ("auto", "auto-efficiency", "auto-intelligence")
    assert COPILOT_AUTO_TIERS == {"auto": "balance", "auto-efficiency": "efficiency", "auto-intelligence": "intelligence"}
    for auto_id in AUTO_MODEL_IDS:
        assert canonicalize_copilot_model_id(auto_id) == auto_id
        assert canonicalize_copilot_model_id("github-copilot/{0} ".format(auto_id.upper())) == auto_id
        assert is_auto_model_id(auto_id)
        assert is_auto_model_id("github-copilot/" + auto_id)
        assert auto_id not in SUPPORTED_COPILOT_MODEL_IDS
    assert canonicalize_copilot_model_id("auto") == AUTO_MODEL_ID
    assert not is_auto_model_id("gpt-6-sol")
    assert not is_auto_model_id("auto-fast")
    assert auto_tier_for("auto") == "balance"
    assert auto_tier_for("github-copilot/auto-intelligence") == "intelligence"
    assert auto_tier_for("gpt-6-luna") is None
    with pytest.raises(ValueError, match="auto-efficiency, auto-intelligence"):
        canonicalize_copilot_model_id("auto-fast")


def test_static_gpt_6_rows_carry_the_gateway_window_and_auto_rows_mirror_the_default():
    windows = {"gpt-6-astra": 1_050_000, "gpt-6-luna": 1_000_000, "gpt-6-sol": 1_000_000, "gpt-6.1-sol": 1_050_000}
    for model in SUPPORTED_COPILOT_MODEL_IDS:
        profile = resolve_model_context_profile(model)
        assert profile.context_window_tokens == windows[model]
        assert profile.default_reserve_tokens == 128_000
    default = resolve_model_context_profile(DEFAULT_MODEL_ID)
    for auto_id in AUTO_MODEL_IDS:
        profile = resolve_model_context_profile(auto_id)
        assert is_catalog_model_context_profile(profile)
        assert profile.model_id == auto_id
        assert profile.context_window_tokens == default.context_window_tokens
        assert profile.default_reserve_tokens == default.default_reserve_tokens
    assert resolve_model_context_profile("github-copilot/auto-efficiency").model_id == "auto-efficiency"
    assert static_model_context_profile("mai-code-1.1-flash") is None
    assert static_model_context_profile("github-copilot/gpt-6-sol") == default


def test_copilot_model_context_profile_reads_the_gateway_limits():
    profile = copilot_model_context_profile(LUNA_CATALOG)
    assert profile is not None
    assert profile.model_id == "gpt-6-luna"
    assert profile.context_window_tokens == 1_000_000
    assert profile.default_reserve_tokens == 128_000
    assert copilot_model_context_profile(LUNA_OLD_SCHEMA).context_window_tokens == 400_000
    # Without an output cap the gap to the prompt cap is the reserve; without
    # either a quarter of the window (at most 128k) is.
    assert copilot_model_context_profile({"id": "x", "capabilities": {"limits": {"max_context_window_tokens": 300_000, "max_prompt_tokens": 250_000}}}).default_reserve_tokens == 50_000
    assert copilot_model_context_profile({"id": "x", "capabilities": {"limits": {"max_context_window_tokens": 100_000}}}).default_reserve_tokens == 25_000
    assert copilot_model_context_profile({"id": "x", "capabilities": {"limits": {"max_context_window_tokens": 1_000_000.0, "max_output_tokens": 128_000}}}).context_window_tokens == 1_000_000
    assert copilot_model_context_profile({"id": "x", "capabilities": {"limits": {"max_context_window_tokens": "big"}}}) is None
    assert copilot_model_context_profile({"id": "x"}) is None
    assert copilot_model_context_profile({"capabilities": {"limits": {"max_context_window_tokens": 10}}}) is None
    assert copilot_model_context_profile(None) is None


def test_preferred_profile_is_the_first_source_and_learned_profiles_override_the_static_rows():
    auto_profile = copilot_model_context_profile(LUNA_AUTO)
    old_profile = copilot_model_context_profile(LUNA_OLD_SCHEMA)
    static_profile = static_model_context_profile("gpt-6-luna")
    # The caller lists its sources by trust; the first one that exists wins,
    # smaller or larger than the others.
    assert preferred_model_context_profile(old_profile, auto_profile, static_profile) == old_profile
    assert preferred_model_context_profile(None, auto_profile, static_profile) == auto_profile
    assert preferred_model_context_profile(None, None, static_profile) == static_profile
    assert preferred_model_context_profile() is None
    assert model_prompt_budget_tokens(auto_profile) == 1_000_000 - 8_000 - 128_000

    learned = copilot_model_context_profile(MAI)
    register_model_context_profile(learned)
    assert resolve_model_context_profile("mai-code-1.1-flash") == learned
    assert resolve_model_context_profile("github-copilot/mai-code-1.1-flash") == learned
    assert is_catalog_model_context_profile(learned)
    # A learned row for a static id wins too (the gateway said less).
    smaller = copilot_model_context_profile(_entry("gpt-6-sol", window=200_000, prompt=100_000, output=100_000))
    register_model_context_profile(smaller)
    assert resolve_model_context_profile("gpt-6-sol") == smaller
    clear_learned_model_context_profiles()
    assert resolve_model_context_profile("gpt-6-sol") == static_model_context_profile("gpt-6-sol")
    assert not is_catalog_model_context_profile(resolve_model_context_profile("mai-code-1.1-flash"))


def test_session_pool_mismatch_counts_as_model_unavailable():
    assert provider_module._is_model_unavailable_message("Requested model not available for session")


# --- transport ---------------------------------------------------------------


def _auto_answer(selected=LUNA_AUTO, **extra):
    answer = {
        "session_token": "sess-1",
        "expires_at": FAR_FUTURE,
        "selected_model": dict(selected),
        "discounted_costs": {selected["id"]: 0.1},
        # Undeclared in VS Code's type, present on the wire; ignored.
        "multi_turn_mode": "anchor",
        "hydra_scores": {"x": 0.5},
    }
    answer.update(extra)
    return answer


def test_transport_routes_the_way_vscode_does(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse(_auto_answer())

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    transport = GitHubCopilotHTTPTransport(token="copilot-token", timeout=12)
    decision = transport.auto_route("Refactor the payment service", tier="balance")
    assert decision == CopilotAutoDecision(
        model="gpt-6-luna",
        session_token="sess-1",
        expires_at=FAR_FUTURE,
        tier="balance",
        metadata=dict(LUNA_AUTO),
        discounted_costs={"gpt-6-luna": 0.1},
    )
    request, timeout = requests[0]
    assert timeout == 12
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.githubcopilot.com/auto"
    assert json.loads(request.data.decode("utf-8")) == {"prompt": "Refactor the payment service", "tier": "balance"}
    assert request.get_header("Authorization") == "Bearer copilot-token"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("X-github-api-version") == COPILOT_AUTO_API_VERSION
    assert request.get_header("Copilot-integration-id") == "vscode-chat"
    assert request.get_header("Editor-version") == "vscode/1.133.0"
    for chat_only in ("Copilot-session-token", "Openai-intent", "X-initiator"):
        assert request.get_header(chat_only) is None
    # Every route is a round trip: the pick depends on the prompt.
    transport.auto_route("  hey ", has_image=True)
    assert len(requests) == 2
    assert json.loads(requests[1][0].data.decode("utf-8")) == {"prompt": "hey", "has_image": True}
    with pytest.raises(ProviderAutoSessionError, match="needs a prompt"):
        transport.auto_route("   ")
    assert len(requests) == 2


def test_transport_route_timeout_is_capped_below_the_chat_timeout(monkeypatch):
    seen = []
    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: seen.append(timeout) or _FakeHTTPResponse(_auto_answer()),
    )
    GitHubCopilotHTTPTransport(token="copilot-token").auto_route("hey")
    GitHubCopilotHTTPTransport(token="copilot-token", timeout=None).auto_route("hey")
    assert seen == [COPILOT_AUTO_ROUTE_TIMEOUT_SECONDS, COPILOT_AUTO_ROUTE_TIMEOUT_SECONDS]


@pytest.mark.parametrize(
    "status, body",
    [
        (404, b"not found"),
        (403, json.dumps({"error": {"message": "auto mode is not enabled for copilot-token"}}).encode("utf-8")),
    ],
)
def test_transport_reports_a_refused_route_without_leaking_the_token(monkeypatch, status, body):
    def fake_urlopen(request, timeout):
        raise urllib_error.HTTPError(request.full_url, status, "refused", hdrs=None, fp=io.BytesIO(body))

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    transport = GitHubCopilotHTTPTransport(token="copilot-token")
    with pytest.raises(ProviderAutoSessionError) as excinfo:
        transport.auto_route("hey")
    assert excinfo.value.status_code == status
    assert f"auto route request failed with HTTP {status}" in str(excinfo.value)
    assert "copilot-token" not in str(excinfo.value)


@pytest.mark.parametrize(
    "answer, fragment",
    [
        ({"expires_at": FAR_FUTURE, "selected_model": {"id": "gpt-6-luna"}}, "no session_token"),
        ({"session_token": "s", "selected_model": {"id": "gpt-6-luna"}}, "invalid expires_at"),
        ({"session_token": "s", "expires_at": FAR_FUTURE}, "no selected_model"),
        ({"session_token": "s", "expires_at": FAR_FUTURE, "selected_model": {"name": "x"}}, "without an id"),
        (b"<html>gateway</html>", "invalid JSON"),
        ([1, 2], "non-object"),
    ],
)
def test_transport_rejects_an_unusable_route_answer(monkeypatch, answer, fragment):
    monkeypatch.setattr(provider_module.urllib_request, "urlopen", lambda request, timeout: _FakeHTTPResponse(answer))
    with pytest.raises(ProviderAutoSessionError, match=fragment):
        GitHubCopilotHTTPTransport(token="copilot-token").auto_route("hey")


def test_transport_fetches_and_caches_the_models_catalog(monkeypatch):
    requests = []
    answers = [{"data": [LUNA_CATALOG, {"id": "GPT-6-Sol ", "capabilities": {}}, "junk"]}]

    def fake_urlopen(request, timeout):
        requests.append(request)
        if not answers:
            raise urllib_error.HTTPError(request.full_url, 500, "down", hdrs=None, fp=io.BytesIO(b""))
        return _FakeHTTPResponse(answers.pop(0))

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    clock = [1_000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: clock[0])
    transport = GitHubCopilotHTTPTransport(token="copilot-token")
    catalog = transport.models_catalog()
    assert set(catalog) == {"gpt-6-luna", "gpt-6-sol"}
    assert catalog["gpt-6-luna"] == LUNA_CATALOG
    request = requests[0]
    assert request.get_method() == "GET"
    assert request.full_url == "https://api.githubcopilot.com/models"
    assert request.data is None
    assert request.get_header("X-github-api-version") == COPILOT_AUTO_API_VERSION
    assert request.get_header("Content-type") is None
    # Cached for an hour; a forced refresh that fails keeps the old answer and
    # is not retried for a while.
    clock[0] += 1_800
    assert transport.models_catalog() is catalog
    assert len(requests) == 1
    assert transport.models_catalog(force=True) == catalog
    assert len(requests) == 2
    clock[0] += 60
    assert transport.models_catalog() == catalog
    assert len(requests) == 2
    clock[0] += 3_600
    assert transport.models_catalog() == catalog
    assert len(requests) == 3


def test_transport_session_token_header_follows_the_setter():
    transport = GitHubCopilotHTTPTransport(token="copilot-token")
    assert "Copilot-Session-Token" not in transport._headers()
    transport.set_auto_session_token("sess-2")
    assert transport._headers()["Copilot-Session-Token"] == "sess-2"
    assert transport._headers(stream=True)["Copilot-Session-Token"] == "sess-2"
    transport.set_auto_session_token(None)
    assert "Copilot-Session-Token" not in transport._headers()


# --- provider ----------------------------------------------------------------


def test_provider_sends_the_picked_model_with_the_session_token_and_registers_its_profile():
    transport = _RoutingTransport([], catalog=(LUNA_CATALOG,))
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    assert provider.model == AUTO_MODEL_ID
    payload = provider.build_payload(_request({"run_id": "run-1"}, prompt="hey"))
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "medium"}
    assert "metadata" not in payload
    assert transport.route_calls == [("hey", "balance", False)]
    assert transport.session_token == "session-1"
    resolution = provider.auto_resolution
    assert resolution.model == "gpt-6-luna"
    assert resolution.session_active is True
    assert resolution.fallback_reason is None
    assert resolution.discount == 0.1
    assert resolution.tier == "balance"
    assert resolution.reasoning_effort == "medium"
    assert resolution.expires_at == FAR_FUTURE
    # The gateway's catalog row is the budget, and the runner's lookup now
    # sees it.
    assert resolution.profile == copilot_model_context_profile(LUNA_CATALOG)
    assert resolve_model_context_profile("gpt-6-luna") == resolution.profile
    # A second payload in the same run reuses the decision.
    provider.build_payload(_request({"run_id": "run-1"}, prompt="hey"))
    assert len(transport.route_calls) == 1
    # A new run routes again, with that run's prompt.
    provider.build_payload(_request({"run_id": "run-2"}, prompt="Refactor everything"))
    assert transport.route_calls[-1] == ("Refactor everything", "balance", False)
    assert transport.session_token == "session-2"


def test_provider_budgets_with_the_catalog_row_over_the_auto_metadata_and_the_static_row():
    # Whatever the catalog says is the budget: the morning's headerless 400k
    # row beats the 1M the /auto answer embeds and the 1M static row.
    transport = _RoutingTransport([], catalog=(LUNA_OLD_SCHEMA,))
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    provider.build_payload(_request({"run_id": "r1"}))
    assert provider.auto_resolution.profile.context_window_tokens == 400_000
    assert resolve_model_context_profile("gpt-6-luna").context_window_tokens == 400_000
    # Without a catalog row the /auto metadata decides; without either, the static row.
    provider = GitHubCopilotProvider(transport=_RoutingTransport([], selected=LUNA_OLD_SCHEMA), model="auto")
    provider.build_payload(_request({"run_id": "r1"}))
    assert provider.auto_resolution.profile.context_window_tokens == 400_000
    provider = GitHubCopilotProvider(transport=_RoutingTransport([], selected={"id": "gpt-6-luna", "capabilities": {"supports": {"tool_calls": True}}}), model="auto")
    provider.build_payload(_request({"run_id": "r1"}))
    assert provider.auto_resolution.profile == static_model_context_profile("gpt-6-luna")


def test_provider_takes_the_tier_from_the_auto_id_and_the_prompt_from_the_member():
    transport = _RoutingTransport([])
    provider = GitHubCopilotProvider(transport=transport, model="auto-intelligence")
    provider.build_payload(_request({"run_id": "r1", "original_user_message": "my words", "attached_image_count": 1}, prompt="composed prompt"))
    assert transport.route_calls == [("my words", "intelligence", True)]
    assert provider.auto_resolution.tier == "intelligence"
    # A per-request override picks its own tier and clears for concrete models.
    provider = GitHubCopilotProvider(transport=_RoutingTransport([]))
    auto_payload = provider.build_payload(_request({"requested_model": "github-copilot/auto-efficiency", "run_id": "r2"}, prompt="hey"))
    assert auto_payload["model"] == "gpt-6-luna"
    assert provider.transport.route_calls == [("hey", "efficiency", False)]
    assert provider.transport.session_token == "session-1"
    concrete_payload = provider.build_payload(_request({"requested_model": "gpt-6-astra"}))
    assert concrete_payload["model"] == "gpt-6-astra"
    assert provider.transport.session_token is None
    assert provider.auto_resolution is None


def test_provider_admits_a_model_outside_the_picker_list_and_clamps_the_effort():
    transport = _RoutingTransport([], selected=MAI)
    provider = GitHubCopilotProvider(transport=transport, model="auto", reasoning_effort="max")
    payload = provider.build_payload(_request({"run_id": "r1"}))
    assert payload["model"] == "mai-code-1.1-flash"
    assert payload["reasoning"] == {"effort": "high"}
    assert provider.auto_resolution.reasoning_effort == "high"
    assert provider.auto_resolution.profile.context_window_tokens == 256_000
    assert resolve_model_context_profile("mai-code-1.1-flash").default_reserve_tokens == 128_000
    # A listed effort is kept; a model without an effort list keeps the configured one.
    provider = GitHubCopilotProvider(transport=_RoutingTransport([], selected=SOL61_AUTO), model="auto", reasoning_effort="low")
    assert provider.build_payload(_request({"run_id": "r1"}))["reasoning"] == {"effort": "low"}
    bare = _entry("gpt-6-sol", window=400_000, prompt=272_000, output=128_000)
    provider = GitHubCopilotProvider(transport=_RoutingTransport([], selected=bare), model="auto", reasoning_effort="xhigh")
    assert provider.build_payload(_request({"run_id": "r1"}))["reasoning"] == {"effort": "xhigh"}


def test_clamp_reasoning_effort_walks_down_then_up_and_never_sends_none():
    clamp = provider_module._clamp_reasoning_effort
    assert clamp("max", ("low", "medium", "high")) == "high"
    assert clamp("low", ("none", "medium", "high")) == "medium"
    assert clamp("medium", ("none",)) == "medium"
    assert clamp("high", None) == "high"
    assert clamp("high", ()) == "high"


@pytest.mark.parametrize(
    "transport, reason_fragment",
    [
        (RecordingTransport([]), "no auto routing support"),
        (
            _RoutingTransport([], error=ProviderAutoSessionError("GitHub Copilot auto route request failed with HTTP 404", status_code=404)),
            "HTTP 404",
        ),
        (_RoutingTransport([], selected=_entry("claude-sonnet-5.5", window=200_000, prompt=150_000, output=50_000, endpoints=("/chat/completions", "/messages"))), "not served on /responses"),
        (_RoutingTransport([], selected=_entry("gpt-6-luna", window=400_000, prompt=272_000, output=128_000, tool_calls=False)), "does not support tool calls"),
        (_RoutingTransport([], selected={"id": "gpt-6-luna", "capabilities": {"type": "embeddings"}}), "not a chat model"),
        (_RoutingTransport([], selected={"id": "mystery-model"}), "no usable context metadata"),
    ],
)
def test_provider_falls_back_to_the_default_model_without_a_session(transport, reason_fragment, caplog):
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    with caplog.at_level("WARNING"):
        payload = provider.build_payload(_request({"run_id": "r1"}))
    assert payload["model"] == DEFAULT_MODEL_ID
    assert getattr(transport, "session_token", None) is None
    assert provider.auto_resolution.session_active is False
    assert provider.auto_resolution.tier == "balance"
    assert provider.auto_resolution.profile == static_model_context_profile(DEFAULT_MODEL_ID)
    assert reason_fragment in provider.auto_resolution.fallback_reason
    assert any(reason_fragment in record.getMessage() for record in caplog.records)


def test_provider_falls_back_without_a_prompt_and_when_the_pick_cannot_hold_the_conversation():
    transport = _RoutingTransport([], selected=MAI)
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    empty = _request({"run_id": "r0"}, prompt="   ")
    assert provider.build_payload(empty)["model"] == DEFAULT_MODEL_ID
    assert "no prompt to route" in provider.auto_resolution.fallback_reason
    assert transport.route_calls == []
    # MAI's prompt budget is 256k - 8k - 128k = 120k tokens; a 150k-token
    # history must not force a compaction on Copilot's behalf.
    resolution = provider.resolve_auto_model(prompt="hey", run_key="r1", history_chars=150_000 * 4)
    assert resolution.model == DEFAULT_MODEL_ID
    assert resolution.fallback_reason == "selected model mai-code-1.1-flash: prompt budget 120000 tokens is below the conversation's ~150000 tokens"
    assert transport.session_token is None
    # The same run never re-routes; a smaller conversation in a new run is admitted.
    assert provider.resolve_auto_model(prompt="hey", run_key="r1", history_chars=10) is resolution
    assert len(transport.route_calls) == 1
    admitted = provider.resolve_auto_model(prompt="hey", run_key="r2", history_chars=100_000 * 4)
    assert admitted.model == "mai-code-1.1-flash"
    assert admitted.session_active is True


def test_provider_routes_again_when_the_session_token_is_about_to_expire(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(provider_module.time, "time", lambda: now[0])
    transport = _RoutingTransport([], expires_at=1_000_000 + 1_000)
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    first = provider.resolve_auto_model(prompt="hey", run_key="r1")
    now[0] = first.expires_at - COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS - 1
    assert provider.resolve_auto_model(prompt="hey", run_key="r1") is first
    now[0] = first.expires_at - COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS + 1
    second = provider.resolve_auto_model(prompt="hey", run_key="r1")
    assert second is not first
    assert len(transport.route_calls) == 2
    assert transport.session_token == "session-2"


def test_provider_transport_error_names_the_resolved_model():
    transport = _RoutingTransport([])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    provider.build_payload(_request({"run_id": "r1"}))
    response = provider._transport_error_response(RuntimeError("boom"))
    assert response["metadata"]["model"] == AUTO_MODEL_ID
    assert response["metadata"]["resolved_model"] == "gpt-6-luna"


def test_latest_user_prompt_prefers_the_members_words_then_the_last_user_message():
    request = _request({"original_user_message": " what I typed "}, prompt="composed")
    assert provider_module._latest_user_prompt(request) == "what I typed"
    request = RuntimeRequest(
        session_id="s",
        messages=[],
        iteration=1,
        max_iterations=1,
        provider_request=ProviderRequest(
            messages=[
                RequestMessage(role="user", parts=[RequestMessagePart(type="text", text="first")]),
                RequestMessage(role="assistant", parts=[RequestMessagePart(type="text", text="reply")]),
                RequestMessage(role="user", parts=[RequestMessagePart(type="text", text="second")]),
                RequestMessage(role="tool", parts=[RequestMessagePart(type="text", text="output")]),
            ],
        ),
    )
    assert provider_module._latest_user_prompt(request) == "second"
    assert provider_module._request_history_chars(request) == len("firstreplysecondoutput")


# --- runner ------------------------------------------------------------------


class _MetadataSpyProvider(GitHubCopilotProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.seen_metadata: list[dict] = []

    def build_payload(self, request):
        self.seen_metadata.append(dict(request.metadata))
        return super().build_payload(request)


def _runner(provider, store=None):
    return RuntimeLoopRunner(
        store=store or InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )


@pytest.mark.asyncio
async def test_runner_routes_each_turn_on_its_prompt_and_budgets_for_the_pick():
    transport = _RoutingTransport([_responses_answer("First."), _responses_answer("Second.")], selected=MAI)
    provider = _MetadataSpyProvider(transport=transport, model="auto-efficiency")
    runner = _runner(provider)
    result = await runner.run(
        session_id="session-auto-run",
        user_text="composed prompt with attachment context",
        metadata={"run_id": "run-1", "original_user_message": "hey", "attached_image_count": 0},
    )
    assert result.status == LoopStatus.COMPLETED
    assert transport.route_calls == [("hey", "efficiency", False)]
    assert transport.payloads[0]["model"] == "mai-code-1.1-flash"
    budget = provider.seen_metadata[0]
    assert budget["auto_routing"] == {"model": "mai-code-1.1-flash", "tier": "efficiency", "session_active": True, "fallback_reason": None}
    assert budget["model_id"] == "mai-code-1.1-flash"
    assert budget["context_window_tokens"] == 256_000
    assert budget["context_reserve_tokens"] == 128_000
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["model"] == "mai-code-1.1-flash"
    assert usage_events[-1].payload["resolved_model"] == "mai-code-1.1-flash"
    assert usage_events[-1].payload["auto_tier"] == "efficiency"
    assert "resolved_model_fallback" not in usage_events[-1].payload
    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == "mai-code-1.1-flash"
    assert metadata["requested_model"] == "auto-efficiency"
    assert metadata["auto_tier"] == "efficiency"
    assert "model_fallback_reason" not in metadata

    # The next turn routes again, on its own words, with the history size.
    transport.selected = LUNA_AUTO
    second = await runner.run(session_id="session-auto-run", user_text="Now refactor it all", metadata={"run_id": "run-2"})
    assert second.status == LoopStatus.COMPLETED
    assert transport.route_calls[-1] == ("Now refactor it all", "efficiency", False)
    assert transport.payloads[1]["model"] == "gpt-6-luna"
    assert provider.seen_metadata[1]["context_window_tokens"] == 1_000_000
    stored = runner.store.read_history("session-auto-run")[-1]
    assert stored.metadata["model"] == "gpt-6-luna"
    assert stored.metadata["requested_model"] == "auto-efficiency"


@pytest.mark.asyncio
async def test_runner_without_routing_support_falls_back_and_says_so():
    transport = RecordingTransport([_responses_answer("Default answer.")])
    provider = _MetadataSpyProvider(transport=transport, model="auto")
    result = await _runner(provider).run(session_id="session-auto-fallback", user_text="Route me.")
    assert result.status == LoopStatus.COMPLETED
    assert transport.payloads[0]["model"] == DEFAULT_MODEL_ID
    assert provider.seen_metadata[0]["auto_routing"]["model"] == DEFAULT_MODEL_ID
    assert provider.seen_metadata[0]["model_id"] == DEFAULT_MODEL_ID
    assert provider.seen_metadata[0]["context_window_tokens"] == 1_000_000
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["resolved_model"] == DEFAULT_MODEL_ID
    assert "no auto routing support" in usage_events[-1].payload["resolved_model_fallback"]
    assert usage_events[-1].payload["auto_tier"] == "balance"
    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == DEFAULT_MODEL_ID
    assert metadata["requested_model"] == "auto"
    assert metadata["auto_tier"] == "balance"
    assert "no auto routing support" in metadata["model_fallback_reason"]


@pytest.mark.asyncio
async def test_runner_with_a_concrete_model_neither_routes_nor_reports_auto_fields():
    transport = _RoutingTransport([_responses_answer("Plain answer.")])
    provider = _MetadataSpyProvider(transport=transport, model="gpt-6-luna")
    result = await _runner(provider).run(session_id="session-concrete", user_text="Hi.")
    assert transport.route_calls == []
    assert transport.payloads[0]["model"] == "gpt-6-luna"
    assert "auto_routing" not in provider.seen_metadata[0]
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["model"] == "gpt-6-luna"
    assert "resolved_model" not in usage_events[-1].payload
    assert "auto_tier" not in usage_events[-1].payload
    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == "gpt-6-luna"
    assert "requested_model" not in metadata
    assert "auto_tier" not in metadata
    assert "model_fallback_reason" not in metadata
