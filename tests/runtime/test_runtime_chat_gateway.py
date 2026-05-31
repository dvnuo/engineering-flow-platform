from __future__ import annotations

import pytest

from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.gateway import runtime_chat


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
                "api_key": "ghp_configtoken123",
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
        model="request-model",
        track_usage=False,
        execution_metadata={
            "runtime_profile": {
                "source": "portal.runtime_profile",
                "config": profile_config,
            }
        },
    )

    runtime_config = captured["config"]
    assert captured["provider_model"] == "request-model"
    assert runtime_config.workspace_root == runtime_chat._runtime_workspace_root()
    assert runtime_config.default_provider_id == "github-copilot"
    assert runtime_config.default_model == "request-model"
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
                "api_key": "ghp_configtoken123",
                "model": "gpt-5-mini",
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
