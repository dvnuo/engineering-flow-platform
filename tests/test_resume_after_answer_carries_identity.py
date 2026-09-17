"""A run resumed by an answer must carry the member's header identity.

The Portal stamps ``X-Portal-User-Id`` / ``X-Portal-User-Name`` on every
proxied request, but the resume path used to drop them. Without them the
session_search scope and the member's notes fail closed for the rest of the
turn: the model would suddenly see no earlier sessions and be unable to keep
a note right after the member answered a card.
"""
from __future__ import annotations

import asyncio
import types

import pytest


@pytest.fixture()
def api():
    from src.gateway import runtime_api

    return runtime_api


def _request(headers: dict[str, str]) -> types.SimpleNamespace:
    return types.SimpleNamespace(headers=headers)


def _resume_kwargs(api, monkeypatch, headers: dict[str, str]) -> dict:
    captured: dict = {}

    async def fake_resume(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "response": ""}

    async def fake_emit(*_a, **_k):
        return None

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(api, "emit_agent_event", fake_emit)
    monkeypatch.setattr(api, "resume_runtime_chat", fake_resume)
    monkeypatch.setattr(api, "global_config", types.SimpleNamespace(llm={"model": "test-model"}))
    monkeypatch.setattr(api, "session_manager", types.SimpleNamespace(mark_runtime_running=noop))
    monkeypatch.setattr(api, "_resolve_runtime_agent_identity", lambda _r: ("agent-1", "Agent"))

    async def run():
        await api._resume_chat_after_user_input(
            _request(headers), session_id="s-identity", execution_metadata={}
        )
        await asyncio.wait_for(asyncio.gather(*list(api._RESUME_TASKS)), timeout=5)

    asyncio.run(run())
    return captured


def test_resume_after_answer_forwards_portal_identity_headers(api, monkeypatch):
    captured = _resume_kwargs(
        api,
        monkeypatch,
        {
            "X-Portal-Author-Source": "portal",
            "X-Portal-User-Id": "user-1",
            "X-Portal-User-Name": "Alice",
        },
    )

    assert captured["portal_user_id"] == "user-1"
    assert captured["portal_user_name"] == "Alice"
    assert captured["interactive"] is True


def test_resume_after_answer_ignores_identity_headers_from_untrusted_callers(api, monkeypatch):
    captured = _resume_kwargs(
        api,
        monkeypatch,
        {"X-Portal-User-Id": "user-1", "X-Portal-User-Name": "Alice"},
    )

    assert captured["portal_user_id"] is None
    assert captured["portal_user_name"] is None
