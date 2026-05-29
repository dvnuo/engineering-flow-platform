from __future__ import annotations

import pytest

from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.gateway import runtime_v2_chat


@pytest.mark.asyncio
async def test_runtime_v2_error_result_raises_sanitized_chat_error_after_recording(monkeypatch):
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

    monkeypatch.setattr(runtime_v2_chat, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(
        runtime_v2_chat,
        "get_runtime_v2_session_manager",
        lambda: _FakeSessionManager(),
    )
    monkeypatch.setattr(runtime_v2_chat, "get_runtime_v2_session_store", lambda: object())
    monkeypatch.setattr(
        runtime_v2_chat.config,
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

    with pytest.raises(runtime_v2_chat.RuntimeV2ChatError) as exc_info:
        await runtime_v2_chat.run_runtime_v2_chat(
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
