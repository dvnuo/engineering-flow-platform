from __future__ import annotations

import json

import pytest

import src.efp_runtime.llm.provider as provider_module
from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.gateway import runtime_chat


TIMEOUT_ENV_KEYS = (
    "EFP_GITHUB_COPILOT_TIMEOUT_SECONDS",
    "EFP_LLM_TIMEOUT_SECONDS",
    "EFP_GITHUB_COPILOT_TIMEOUT_MS",
    "EFP_LLM_TIMEOUT_MS",
)


def _clear_timeout_env(monkeypatch):
    for key in TIMEOUT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_runtime_chat_workspace_root_uses_runtime_default(monkeypatch):
    class _FakeConfig:
        def get_effective_config(self):
            return {}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    assert runtime_chat._runtime_workspace_root() == runtime_chat.Path("/workspace").resolve()


def test_runtime_chat_workspace_root_allows_config_override(monkeypatch, tmp_path):
    class _FakeConfig:
        def get_effective_config(self):
            return {"workspace": {"path": str(tmp_path)}}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    assert runtime_chat._runtime_workspace_root() == tmp_path.resolve()


def test_runtime_chat_workspace_root_treats_legacy_config_as_default(monkeypatch):
    class _FakeConfig:
        def get_effective_config(self):
            return {"workspace": {"path": "/root/.efp/workspace"}}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    assert runtime_chat._runtime_workspace_root() == runtime_chat.Path("/workspace").resolve()


def test_runtime_chat_resolves_default_and_alias_models(monkeypatch):
    class _FakeConfig:
        @property
        def llm(self):
            return {"model": "gpt-5.6 terra"}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    assert runtime_chat._resolve_model(None) == "gpt-5.6-terra"
    assert runtime_chat._resolve_model("gpt-5.6 luna") == "gpt-5.6-luna"


def test_runtime_chat_rejects_invalid_model_locally(monkeypatch):
    class _FakeConfig:
        @property
        def llm(self):
            return {"model": "gpt-5"}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    with pytest.raises(runtime_chat.RuntimeChatError) as exc_info:
        runtime_chat._resolve_model(None)

    assert exc_info.value.status_code == 400
    assert exc_info.value.error_type == "invalid_model"


def test_build_github_copilot_provider_uses_responses_and_config_reasoning(monkeypatch):
    monkeypatch.delenv("EFP_GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_COPILOT_TOKEN", raising=False)
    monkeypatch.delenv("EFP_GITHUB_COPILOT_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("EFP_LLM_REASONING_EFFORT", raising=False)
    _clear_timeout_env(monkeypatch)
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeHTTPResponse(
            {
                "token": (
                    "copilot-config-token;"
                    "proxy-ep=https%3A%2F%2Fproxy.individual.githubcopilot.com"
                ),
                "expires_at": 1893456000,
            }
        )

    monkeypatch.setattr(provider_module.urllib_request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {
            "llm": {
                "provider": "github_copilot",
                "api_key": "ghp_configtoken123",
                "api_base": "https://copilot-api.enterprise.example/",
                "reasoning_effort": "xhigh",
            }
        },
        raising=False,
    )

    provider = runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert provider.endpoint == "responses"
    assert provider.stream is True
    assert provider.reasoning_effort == "xhigh"
    assert provider.transport.endpoint == "https://copilot-api.enterprise.example/responses"
    assert provider.transport.timeout == 300
    assert provider.transport.token_source == "github_exchange"
    assert provider.transport._headers()["Authorization"] == (
        "Bearer copilot-config-token;"
        "proxy-ep=https%3A%2F%2Fproxy.individual.githubcopilot.com"
    )
    assert provider.transport._headers()["Accept"] == (
        "application/vnd.github.copilot-chat-preview+json"
    )
    assert provider.transport._headers()["x-initiator"] == "agent"
    assert len(requests) == 1
    exchange_request, exchange_timeout = requests[0]
    assert exchange_timeout == 300
    assert exchange_request.full_url == (
        "https://api.github.com/copilot_internal/v2/token"
    )
    exchange_headers = _request_headers(exchange_request)
    assert exchange_headers["authorization"] == "Bearer ghp_configtoken123"
    assert exchange_headers["accept"] == "application/json"
    assert exchange_headers["user-agent"] == "GitHubCopilotChat/0.35.0"
    assert exchange_headers["editor-version"] == "vscode/1.107.0"
    assert exchange_headers["editor-plugin-version"] == "copilot-chat/0.35.0"
    assert exchange_headers["copilot-integration-id"] == "vscode-chat"
    assert "openai-intent" not in exchange_headers
    assert "x-initiator" not in exchange_headers


def test_build_github_copilot_provider_prefers_env_reasoning(monkeypatch):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    monkeypatch.setenv("EFP_GITHUB_COPILOT_REASONING_EFFORT", "low")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {
            "llm": {
                "provider": "github_copilot",
                "api_key": "ghp_configtoken123",
                "reasoning_effort": "high",
            }
        },
        raising=False,
    )

    provider = runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert provider.reasoning_effort == "low"


def test_build_github_copilot_provider_rejects_invalid_reasoning(monkeypatch):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", "reasoning_effort": "extreme"}},
        raising=False,
    )

    with pytest.raises(runtime_chat.RuntimeChatError) as exc_info:
        runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert exc_info.value.status_code == 400
    assert exc_info.value.error_type == "invalid_reasoning_effort"


@pytest.mark.parametrize(
    ("timeout_config", "expected_timeout"),
    [
        ({"timeout_ms": 600000}, 600),
        ({"timeout_seconds": 120}, 120),
        ({"timeout": 450000}, 450),
        ({"request_timeout_seconds": 180}, 180),
    ],
)
def test_build_github_copilot_provider_resolves_configured_timeout(
    monkeypatch,
    timeout_config,
    expected_timeout,
):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", **timeout_config}},
        raising=False,
    )

    provider = runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert provider.transport.timeout == expected_timeout


def test_build_github_copilot_provider_prefers_env_timeout_over_config(monkeypatch):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setenv("EFP_LLM_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TIMEOUT_MS", "600000")
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", "timeout_seconds": 120}},
        raising=False,
    )

    provider = runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert provider.transport.timeout == 90


@pytest.mark.parametrize(
    ("timeout_config", "env_name", "env_value"),
    [
        ({"timeout": False}, None, None),
        ({"timeout_ms": 600000}, "EFP_GITHUB_COPILOT_TIMEOUT_SECONDS", "false"),
        ({"timeout_ms": 600000}, "EFP_LLM_TIMEOUT_MS", "off"),
    ],
)
def test_build_github_copilot_provider_can_disable_timeout(
    monkeypatch,
    timeout_config,
    env_name,
    env_value,
):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    if env_name is not None:
        monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", **timeout_config}},
        raising=False,
    )

    provider = runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert provider.transport.timeout is None


@pytest.mark.parametrize("timeout_config", [{"timeout_ms": "abc"}, {"timeout_ms": 0}])
def test_build_github_copilot_provider_rejects_invalid_timeout_config(
    monkeypatch,
    timeout_config,
):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", **timeout_config}},
        raising=False,
    )

    with pytest.raises(runtime_chat.RuntimeChatError) as exc_info:
        runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert exc_info.value.status_code == 400
    assert exc_info.value.error_type == "invalid_timeout"
    assert exc_info.value.details["provider"] == "github-copilot"


def test_build_github_copilot_provider_rejects_invalid_timeout_env(monkeypatch):
    monkeypatch.setenv("EFP_GITHUB_COPILOT_TOKEN", "env-token")
    _clear_timeout_env(monkeypatch)
    monkeypatch.setenv("EFP_LLM_TIMEOUT_SECONDS", "0")
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", "timeout_seconds": 120}},
        raising=False,
    )

    with pytest.raises(runtime_chat.RuntimeChatError) as exc_info:
        runtime_chat._build_github_copilot_provider("gpt-5.4")

    assert exc_info.value.status_code == 400
    assert exc_info.value.error_type == "invalid_timeout"
    assert exc_info.value.details["provider"] == "github-copilot"


@pytest.mark.asyncio
async def test_runtime_chat_applies_trusted_portal_runtime_profile_config(monkeypatch):
    captured = {}

    def _fake_provider(model):
        captured["provider_model"] = model
        return object()

    class _FakeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, _prompt, *, session_id, metadata=None):
            captured["run_metadata"] = metadata
            return RuntimeLoopResult(
                session_id=session_id,
                final_assistant_message=None,
                iterations=1,
                status=LoopStatus.COMPLETED,
            )

    class _FakeSessionManager:
        async def record_runtime_result(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(runtime_chat, "_build_github_copilot_provider", _fake_provider)
    monkeypatch.setattr(runtime_chat, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(
        runtime_chat,
        "get_runtime_session_manager",
        lambda: _FakeSessionManager(),
    )
    monkeypatch.setattr(runtime_chat, "get_runtime_session_store", lambda: object())
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {
            "llm": {
                "provider": "github_copilot",
                "api_key": "copilot-config-token",
                "model": "gpt-5-mini",
            },
            "session": {"max_iterations": 2},
        },
        raising=False,
    )

    profile_config = {
        "enabled_tools": ["read"],
        "disabled_tools": ["write"],
        "tool_permissions": {"bash": "ask"},
        "active_skills": ["review"],
        "command_directories": ["/workspace/.efp/commands"],
        "compaction_auto": False,
        "max_context_tokens": 64000,
        "system_prompt_texts": ["system"],
        "instruction_texts": ["instruction"],
        "runtime_mode": "plan",
        "enable_plan_tool": True,
        "tool_output_max_lines": 100,
        "archive_truncated_tool_outputs": False,
        "workspace_root": "/portal/workspace",
        "default_provider_id": "openai",
        "default_model": "portal-model",
        "compaction_preserve_recent_turns": 8,
        "unknown_future_key": "ignored",
    }

    await runtime_chat.run_runtime_chat(
        message="hello",
        session_id="s-profile",
        request_id="req-profile",
        model="gpt-5.6 terra",
        track_usage=False,
        execution_metadata={
            "runtime_profile": {
                "source": "portal.runtime_profile",
                "config": profile_config,
            }
        },
    )

    runtime_config = captured["config"]
    assert captured["provider_model"] == "gpt-5.6-terra"
    assert runtime_config.workspace_root == runtime_chat._runtime_workspace_root()
    assert runtime_config.default_provider_id == "github-copilot"
    assert runtime_config.default_model == "gpt-5.6-terra"
    assert runtime_config.track_usage is False
    assert runtime_config.enabled_tools == ["read"]
    assert runtime_config.disabled_tools == ["write"]
    assert runtime_config.tool_permissions == {"bash": "ask"}
    assert runtime_config.active_skills == ["review"]
    assert runtime_config.command_directories == ["/workspace/.efp/commands"]
    assert runtime_config.compaction_auto is False
    assert runtime_config.max_context_tokens == 64000
    assert runtime_config.system_prompt_texts == ["system"]
    assert runtime_config.instruction_texts == ["instruction"]
    assert runtime_config.runtime_mode == "plan"
    assert runtime_config.enable_plan_tool is True
    assert runtime_config.tool_output_max_lines == 100
    assert runtime_config.archive_truncated_tool_outputs is False
    assert not hasattr(runtime_config, "compaction_preserve_recent_turns")


def test_runtime_chat_ignores_untrusted_runtime_profile_metadata(monkeypatch):
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"session": {"max_iterations": 2}},
        raising=False,
    )

    runtime_config = runtime_chat._runtime_config(
        "request-model",
        track_usage=True,
        execution_metadata={
            "runtime_profile": {
                "config": {
                    "enabled_tools": ["read"],
                    "track_usage": False,
                }
            }
        },
    )

    assert runtime_config.enabled_tools is None
    assert runtime_config.track_usage is True


def test_runtime_chat_uses_persisted_runtime_config_fields(monkeypatch):
    class _FakeConfig:
        @property
        def session(self):
            return {"max_iterations": 2}

        def get_effective_config(self):
            return {
                "enabled_tools": ["read"],
                "max_context_tokens": 32000,
                "track_usage": False,
                "workspace_root": "/portal/workspace",
                "default_provider_id": "openai",
                "default_model": "portal-model",
                "compaction_preserve_recent_turns": 8,
            }

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    runtime_config = runtime_chat._runtime_config(
        "request-model",
        track_usage=True,
    )

    assert runtime_config.enabled_tools == ["read"]
    assert runtime_config.max_context_tokens == 32000
    assert runtime_config.track_usage is False
    assert runtime_config.workspace_root == runtime_chat._runtime_workspace_root()
    assert runtime_config.default_provider_id == "github-copilot"
    assert runtime_config.default_model == "request-model"
    assert not hasattr(runtime_config, "compaction_preserve_recent_turns")


def test_runtime_chat_leaves_max_iterations_unbounded_when_unconfigured(monkeypatch):
    class _FakeConfig:
        @property
        def session(self):
            return {}

        def get_effective_config(self):
            return {}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())

    runtime_config = runtime_chat._runtime_config(
        "request-model",
        track_usage=True,
    )

    assert runtime_config.max_iterations is None


def test_runtime_chat_adds_default_skill_directories(monkeypatch, tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    class _FakeConfig:
        @property
        def session(self):
            return {"max_iterations": 2}

        def get_effective_config(self):
            return {}

    monkeypatch.setattr(runtime_chat, "config", _FakeConfig())
    monkeypatch.setattr(
        runtime_chat,
        "default_skill_directories",
        lambda workspace_root: [skills_dir],
    )

    runtime_config = runtime_chat._runtime_config(
        "request-model",
        track_usage=False,
    )

    assert runtime_config.skill_directories == [skills_dir]


def test_runtime_chat_profile_track_usage_overrides_only_when_present(monkeypatch):
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"session": {"max_iterations": 2}},
        raising=False,
    )

    without_profile_track_usage = runtime_chat._runtime_config(
        "request-model",
        track_usage=False,
        runtime_profile_config={"enabled_tools": ["read"]},
    )
    with_profile_track_usage = runtime_chat._runtime_config(
        "request-model",
        track_usage=False,
        runtime_profile_config={"track_usage": True},
    )

    assert without_profile_track_usage.track_usage is False
    assert with_profile_track_usage.track_usage is True


@pytest.mark.asyncio
async def test_runtime_error_result_raises_sanitized_chat_error_after_recording(monkeypatch):
    result = RuntimeLoopResult(
        session_id="s-error",
        final_assistant_message=None,
        iterations=1,
        status=LoopStatus.ERROR,
        runtime_events=[
            RuntimeEvent(
                type="llm.error",
                session_id="s-error",
                payload={"error": "Provider failed with token=ghp_supersecret123"},
            ),
            RuntimeEvent(
                type="error",
                session_id="s-error",
                message="Provider emitted an error.",
                payload={},
            ),
        ],
    )
    recorded: list[tuple[str, RuntimeLoopResult, str | None]] = []

    class _FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            return result

    class _FakeSessionManager:
        async def record_runtime_result(self, session_id, runtime_result, *, request_id=None):
            recorded.append((session_id, runtime_result, request_id))

    monkeypatch.setattr(runtime_chat, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(
        runtime_chat,
        "get_runtime_session_manager",
        lambda: _FakeSessionManager(),
    )
    monkeypatch.setattr(runtime_chat, "get_runtime_session_store", lambda: object())
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {
            "llm": {
                "provider": "github_copilot",
                "api_key": "copilot-config-token",
                "model": "gpt-5.6-terra",
            },
            "session": {"max_iterations": 1},
        },
        raising=False,
    )

    with pytest.raises(runtime_chat.RuntimeChatError) as exc_info:
        await runtime_chat.run_runtime_chat(
            message="hello",
            session_id="s-error",
            user_name="u1",
            request_id="req-error",
        )

    assert recorded == [("s-error", result, "req-error")]
    assert exc_info.value.status_code == 502
    assert exc_info.value.error_type == "provider_error"
    assert "Provider failed" in exc_info.value.message
    assert "ghp_supersecret123" not in exc_info.value.message
    assert "***REDACTED***" in exc_info.value.message


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def _request_headers(request):
    headers = {key.lower(): value for key, value in request.header_items()}
    for source in (request.headers, request.unredirected_hdrs):
        headers.update({key.lower(): value for key, value in source.items()})
    return headers
