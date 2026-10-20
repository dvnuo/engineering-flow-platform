"""Copilot ``auto`` is a routing mode, not a model id.

For an ``auto`` request the runtime opens an auto session (``POST
/models/session``), sends the first preferred GPT-6 model Copilot lists as
available together with the ``Copilot-Session-Token`` header, and degrades to
the default model without the session whenever any of that is unavailable.
"""
from __future__ import annotations

import io
import json
from urllib import error as urllib_error

import pytest

import efp_runtime.llm.provider as provider_module
from efp_runtime.llm.models import (
    AUTO_MODEL_ID,
    COPILOT_AUTO_PREFERENCE,
    DEFAULT_MODEL_ID,
    SUPPORTED_COPILOT_MODEL_IDS,
    canonicalize_copilot_model_id,
    is_auto_model_id,
    is_catalog_model_context_profile,
    resolve_model_context_profile,
    select_auto_model,
)
from efp_runtime.llm.provider import (
    COPILOT_AUTO_SESSION_API_VERSION,
    COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS,
    CopilotAutoSession,
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

# What Copilot's docs list for auto in October 2026, minus the models the
# runtime cannot drive being irrelevant to the pick order.
POOL = ("claude-sonnet-5.5", "gpt-6-luna", "gpt-6.1-sol", "gemini-3.8-flash", "gpt-6-sol")


def _request(metadata=None) -> RuntimeRequest:
    return RuntimeRequest(
        session_id="session-auto",
        messages=[],
        iteration=1,
        max_iterations=2,
        provider_request=ProviderRequest(
            messages=[RequestMessage(role="user", parts=[RequestMessagePart(type="text", text="Hello")])],
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


class _SessionTransport(RecordingTransport):
    """RecordingTransport that also speaks the auto-session protocol."""

    def __init__(self, responses, *, pool=POOL, error=None, discounts=None):
        super().__init__(responses)
        self.pool = tuple(pool)
        self.error = error
        self.discounts = dict(discounts or {})
        self.session_calls = 0
        self.session_token = None

    def auto_session(self, *, force: bool = False) -> CopilotAutoSession:
        self.session_calls += 1
        if self.error is not None:
            raise self.error
        return CopilotAutoSession(
            session_token="session-abc",
            expires_at=4_102_444_800,
            available_models=self.pool,
            discounted_costs=self.discounts,
        )

    def set_auto_session_token(self, token):
        self.session_token = token


# --- catalog -----------------------------------------------------------------


def test_auto_is_accepted_as_a_copilot_model_id_but_is_not_a_concrete_model():
    assert canonicalize_copilot_model_id("auto") == AUTO_MODEL_ID
    assert canonicalize_copilot_model_id("github-copilot/Auto ") == AUTO_MODEL_ID
    assert AUTO_MODEL_ID not in SUPPORTED_COPILOT_MODEL_IDS
    assert is_auto_model_id("github-copilot/auto")
    assert not is_auto_model_id("gpt-6-sol")
    with pytest.raises(ValueError, match="unsupported GitHub Copilot model"):
        canonicalize_copilot_model_id("gpt-5.6-sol")


def test_auto_preference_covers_exactly_the_supported_gpt_6_line():
    assert set(COPILOT_AUTO_PREFERENCE) == set(SUPPORTED_COPILOT_MODEL_IDS)
    assert COPILOT_AUTO_PREFERENCE[0] == "gpt-6.1-sol"


def test_select_auto_model_prefers_in_order_and_skips_models_the_runtime_cannot_drive():
    assert select_auto_model(POOL) == "gpt-6.1-sol"
    assert select_auto_model(("claude-opus-5.5", "github-copilot/gpt-6 luna", "gpt-6-sol")) == "gpt-6-sol"
    assert select_auto_model(("gpt-6-astra",)) == "gpt-6-astra"
    assert select_auto_model(("claude-sonnet-5.5", "gemini-3.8-flash", "gpt-5.3-codex", "gpt-5.6-terra")) is None
    assert select_auto_model(()) is None
    assert select_auto_model(None) is None


def test_auto_profile_is_the_smallest_window_auto_can_pick():
    profile = resolve_model_context_profile(AUTO_MODEL_ID)

    assert is_catalog_model_context_profile(profile)
    assert profile.context_window_tokens == min(
        resolve_model_context_profile(model).context_window_tokens for model in COPILOT_AUTO_PREFERENCE
    )
    assert profile.default_reserve_tokens == 128_000
    assert resolve_model_context_profile("github-copilot/auto").model_id == AUTO_MODEL_ID


def test_session_pool_mismatch_counts_as_model_unavailable():
    assert provider_module._is_model_unavailable_message("Requested model not available for session")


# --- transport ---------------------------------------------------------------


def test_transport_opens_an_auto_session_the_way_vscode_does(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse(
            {
                "session_token": "sess-1",
                "expires_at": 4_102_444_800,
                "available_models": list(POOL),
                "discounted_costs": {"gpt-6.1-sol": 0.1},
            }
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    transport = GitHubCopilotHTTPTransport(token="copilot-token", timeout=12)

    session = transport.auto_session()

    assert session == CopilotAutoSession("sess-1", 4_102_444_800, POOL, {"gpt-6.1-sol": 0.1})
    request, timeout = requests[0]
    assert timeout == 12
    assert request.get_method() == "POST"
    assert request.full_url == "https://api.githubcopilot.com/models/session"
    assert json.loads(request.data.decode("utf-8")) == {"auto_mode": {"model_hints": ["auto"]}}
    assert request.get_header("Authorization") == "Bearer copilot-token"
    assert request.get_header("Copilot-integration-id") == "vscode-chat"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("X-github-api-version") == COPILOT_AUTO_SESSION_API_VERSION
    assert request.get_header("Copilot-session-token") is None
    # Chat-only headers are not part of the session request.
    assert request.get_header("Openai-intent") is None
    assert request.get_header("X-initiator") is None
    # Reused while valid: no second round trip.
    assert transport.auto_session() is session
    assert len(requests) == 1


def test_transport_refreshes_the_session_near_expiry_and_sends_the_header(monkeypatch):
    now = [1_000_000]
    count = {"n": 0}

    def fake_urlopen(request, timeout):
        count["n"] += 1
        return _FakeHTTPResponse(
            {"session_token": f"sess-{count['n']}", "expires_at": now[0] + 1000, "available_models": ["gpt-6-sol"]}
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    monkeypatch.setattr(provider_module.time, "time", lambda: now[0])
    transport = GitHubCopilotHTTPTransport(token="copilot-token")

    first = transport.auto_session()
    # Still more than the margin away from expiry: reused.
    now[0] = first.expires_at - COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS - 1
    assert transport.auto_session() is first
    # Inside the margin: reopened, like VS Code's five-minute rule.
    now[0] = first.expires_at - COPILOT_AUTO_SESSION_REFRESH_MARGIN_SECONDS + 1
    second = transport.auto_session()
    assert second.session_token == "sess-2"
    assert count["n"] == 2

    transport.set_auto_session_token(second.session_token)
    assert transport._headers()["Copilot-Session-Token"] == "sess-2"
    assert transport._headers(stream=True)["Copilot-Session-Token"] == "sess-2"
    transport.set_auto_session_token(None)
    assert "Copilot-Session-Token" not in transport._headers()


@pytest.mark.parametrize(
    "status, body",
    [
        (404, b"not found"),
        (403, json.dumps({"error": {"message": "auto mode is not enabled for copilot-token"}}).encode("utf-8")),
    ],
)
def test_transport_reports_a_refused_session_without_leaking_the_token(monkeypatch, status, body):
    def fake_urlopen(request, timeout):
        raise urllib_error.HTTPError(request.full_url, status, "refused", hdrs=None, fp=io.BytesIO(body))

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    transport = GitHubCopilotHTTPTransport(token="copilot-token")

    with pytest.raises(ProviderAutoSessionError) as excinfo:
        transport.auto_session()

    assert excinfo.value.status_code == status
    assert f"HTTP {status}" in str(excinfo.value)
    assert "copilot-token" not in str(excinfo.value)


def test_transport_rejects_a_session_answer_without_a_token(monkeypatch):
    monkeypatch.setattr(
        provider_module.urllib_request,
        "urlopen",
        lambda request, timeout: _FakeHTTPResponse({"available_models": ["gpt-6-sol"], "expires_at": 4_102_444_800}),
    )
    transport = GitHubCopilotHTTPTransport(token="copilot-token")

    with pytest.raises(ProviderAutoSessionError, match="no session_token"):
        transport.auto_session()


# --- provider ----------------------------------------------------------------


def test_provider_routes_auto_to_the_preferred_pool_model_with_the_session_token():
    transport = _SessionTransport([], discounts={"gpt-6.1-sol": 0.1})
    provider = GitHubCopilotProvider(transport=transport, model="auto")

    assert provider.model == AUTO_MODEL_ID
    payload = provider.build_payload(_request())

    assert payload["model"] == "gpt-6.1-sol"
    assert payload["reasoning"] == {"effort": "high"}
    assert "metadata" not in payload
    assert transport.session_token == "session-abc"
    assert provider.auto_resolution.model == "gpt-6.1-sol"
    assert provider.auto_resolution.session_active is True
    assert provider.auto_resolution.fallback_reason is None
    assert provider.auto_resolution.discount == 0.1


def test_provider_routes_a_per_request_auto_override_and_clears_it_for_concrete_models():
    transport = _SessionTransport([], pool=("gpt-6-luna", "claude-opus-5.5"))
    provider = GitHubCopilotProvider(transport=transport)

    auto_payload = provider.build_payload(_request({"requested_model": "github-copilot/auto"}))
    assert auto_payload["model"] == "gpt-6-luna"
    assert transport.session_token == "session-abc"

    concrete_payload = provider.build_payload(_request({"requested_model": "gpt-6-astra"}))
    assert concrete_payload["model"] == "gpt-6-astra"
    assert transport.session_token is None
    assert provider.auto_resolution is None


@pytest.mark.parametrize(
    "transport, reason_fragment",
    [
        (RecordingTransport([]), "no auto-mode session support"),
        (
            _SessionTransport(
                [],
                error=ProviderAutoSessionError(
                    "GitHub Copilot auto session request failed with HTTP 404", status_code=404
                ),
            ),
            "HTTP 404",
        ),
        (_SessionTransport([], pool=("claude-sonnet-5.5", "gemini-3.8-flash")), "no supported GPT model"),
    ],
)
def test_provider_falls_back_to_the_default_model_without_a_session(transport, reason_fragment, caplog):
    provider = GitHubCopilotProvider(transport=transport, model="auto")

    with caplog.at_level("WARNING"):
        payload = provider.build_payload(_request())

    assert payload["model"] == DEFAULT_MODEL_ID
    assert getattr(transport, "session_token", None) is None
    assert provider.auto_resolution.session_active is False
    assert reason_fragment in provider.auto_resolution.fallback_reason
    assert any(reason_fragment in record.getMessage() for record in caplog.records)


def test_provider_transport_error_names_the_resolved_model():
    transport = _SessionTransport([])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    provider.build_payload(_request())

    response = provider._transport_error_response(RuntimeError("boom"))

    assert response["metadata"]["model"] == AUTO_MODEL_ID
    assert response["metadata"]["resolved_model"] == "gpt-6.1-sol"


# --- runner ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_sends_the_routed_model_and_reports_it_on_usage_events():
    transport = _SessionTransport([_responses_answer("Routed answer.")])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-auto-run", user_text="Route me.")

    assert result.status == LoopStatus.COMPLETED
    assert transport.payloads[0]["model"] == "gpt-6.1-sol"
    assert transport.session_calls >= 1
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events
    assert usage_events[-1].payload["resolved_model"] == "gpt-6.1-sol"
    assert "resolved_model_fallback" not in usage_events[-1].payload


@pytest.mark.asyncio
async def test_runner_without_session_support_falls_back_and_says_so():
    transport = RecordingTransport([_responses_answer("Default answer.")])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-auto-fallback", user_text="Route me.")

    assert result.status == LoopStatus.COMPLETED
    assert transport.payloads[0]["model"] == DEFAULT_MODEL_ID
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["resolved_model"] == DEFAULT_MODEL_ID
    assert "no auto-mode session support" in usage_events[-1].payload["resolved_model_fallback"]


@pytest.mark.asyncio
async def test_runner_with_a_concrete_model_reports_no_resolved_model():
    transport = RecordingTransport([_responses_answer("Plain answer.")])
    provider = GitHubCopilotProvider(transport=transport, model="gpt-6-luna")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-concrete", user_text="Hi.")

    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events
    assert "resolved_model" not in usage_events[-1].payload
# --- attribution ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_assistant_message_records_the_model_that_answered_for_auto():
    transport = _SessionTransport([_responses_answer("Routed.")])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-attrib-auto", user_text="Hi.")

    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == "gpt-6.1-sol"
    assert metadata["requested_model"] == "auto"
    assert "model_fallback_reason" not in metadata
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["model"] == "gpt-6.1-sol"
    # Persisted with the message, so a reloaded transcript shows it too.
    stored = runner.store.read_history("session-attrib-auto")[-1]
    assert stored.metadata["model"] == "gpt-6.1-sol"
    assert stored.metadata["requested_model"] == "auto"


@pytest.mark.asyncio
async def test_assistant_message_records_the_fallback_when_auto_had_no_session():
    transport = RecordingTransport([_responses_answer("Default.")])
    provider = GitHubCopilotProvider(transport=transport, model="auto")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-attrib-fallback", user_text="Hi.")

    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == DEFAULT_MODEL_ID
    assert metadata["requested_model"] == "auto"
    assert "no auto-mode session support" in metadata["model_fallback_reason"]


@pytest.mark.asyncio
async def test_assistant_message_records_a_concrete_model_without_auto_fields():
    transport = RecordingTransport([_responses_answer("Plain.")])
    provider = GitHubCopilotProvider(transport=transport, model="gpt-6-luna")
    runner = RuntimeLoopRunner(
        store=InMemorySessionStore(),
        provider=provider,
        tool_runtime=ToolRuntime(ToolRegistry()),
    )

    result = await runner.run(session_id="session-attrib-concrete", user_text="Hi.")

    metadata = result.final_assistant_message.metadata
    assert metadata["model"] == "gpt-6-luna"
    assert "requested_model" not in metadata
    assert "model_fallback_reason" not in metadata
    usage_events = [event for event in result.runtime_events if event.type == "usage.updated"]
    assert usage_events[-1].payload["model"] == "gpt-6-luna"
    assert "resolved_model" not in usage_events[-1].payload
