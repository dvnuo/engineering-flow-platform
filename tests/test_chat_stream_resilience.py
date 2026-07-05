"""Regressions for long-running chat stream resilience.

Covers: SSE keepalive comments during idle streaming, startup sweep of
interrupted running chat sessions, and /api/chat request_id idempotency.
"""

import asyncio

import pytest

from src.efp_runtime.session.gateway_facade import RuntimeSessionManager
from src.gateway import runtime_api


class _RecordingStreamResponse:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {}
        self.writes = []

    async def prepare(self, request):
        return self

    async def write(self, data):
        self.writes.append(data.decode())


class _PortalRequest:
    app = {}
    headers = {"X-Portal-Author-Source": "portal"}

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _patch_stream_harness(monkeypatch, fake_run):
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", fake_run)
    monkeypatch.setattr(runtime_api, "_emit_gateway_runtime_event", lambda _payload: asyncio.sleep(0, result=None))
    monkeypatch.setattr(runtime_api, "publish_session_metadata", lambda **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "mark_runtime_running", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(runtime_api.web, "StreamResponse", _RecordingStreamResponse)
    monkeypatch.setattr(
        runtime_api.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )


@pytest.mark.asyncio
async def test_api_chat_stream_writes_keepalive_comments_while_idle(monkeypatch):
    request_id = "portal-stream-keepalive-req-1"
    session_id = "s-stream-keepalive"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    # The interval floor is 1s; simulate an idle window slightly above it.
    monkeypatch.setenv("EFP_CHAT_SSE_KEEPALIVE_SECONDS", "1")

    async def _fake_run_chat_via_execution_bus(**kwargs):
        # Simulate a long tool execution: no runtime events for a while.
        await asyncio.sleep(1.4)
        return {"response": "done after quiet tool run", "usage": {}}

    _patch_stream_harness(monkeypatch, _fake_run_chat_via_execution_bus)

    try:
        response = await runtime_api.api_chat_stream(
            _PortalRequest({"message": "long task", "session_id": session_id, "client_request_id": request_id})
        )
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    keepalive_chunks = [chunk for chunk in response.writes if chunk.startswith(": keepalive")]
    assert keepalive_chunks, "expected SSE keepalive comments during idle streaming"
    assert all(chunk.endswith("\n\n") for chunk in keepalive_chunks)
    assert any("event: final" in chunk for chunk in response.writes)
    assert any("event: done" in chunk for chunk in response.writes)
    # Keepalive comments must never carry event/data fields.
    assert all("data:" not in chunk for chunk in keepalive_chunks)


@pytest.mark.asyncio
async def test_api_chat_stream_does_not_write_keepalive_when_events_flow(monkeypatch):
    request_id = "portal-stream-busy-req-1"
    session_id = "s-stream-busy"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    monkeypatch.setenv("EFP_CHAT_SSE_KEEPALIVE_SECONDS", "60")

    async def _fake_run_chat_via_execution_bus(**kwargs):
        await kwargs["stream_callback"].put(
            {"type": "runtime.event", "event_type": "runtime.event", "summary": "p", "created_at": "2026-07-05T00:00:00Z"}
        )
        return {"response": "quick", "usage": {}}

    _patch_stream_harness(monkeypatch, _fake_run_chat_via_execution_bus)

    try:
        response = await runtime_api.api_chat_stream(
            _PortalRequest({"message": "quick task", "session_id": session_id, "client_request_id": request_id})
        )
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    assert not any(chunk.startswith(": keepalive") for chunk in response.writes)
    assert any("event: final" in chunk for chunk in response.writes)


@pytest.mark.asyncio
async def test_mark_interrupted_running_sessions_expires_stale_running_state(tmp_path):
    manager = RuntimeSessionManager(root=tmp_path)

    manager.store.create_session(session_id="s-running")
    await manager.mark_runtime_running("s-running", request_id="req-running")

    manager.store.create_session(session_id="s-completed")
    await manager.merge_metadata(
        "s-completed",
        {
            "last_runtime_status": "success",
            "latest_event_state": "success",
            "completion_state": "completed",
        },
    )

    manager.store.create_session(session_id="s-plain")

    # Blocked runs waiting for a user response keep latest_event_state
    # "running" but are resumable across restarts; they must not be expired.
    manager.store.create_session(session_id="s-blocked-permission")
    await manager.mark_runtime_running("s-blocked-permission", request_id="req-blocked")
    await manager.merge_metadata(
        "s-blocked-permission",
        {
            "last_runtime_status": "permission_requested",
            "pending_permission_request": {"tool_name": "bash", "call_id": "call-1"},
        },
    )

    # Defensive: even an inconsistent "running" status with a pending user
    # request must not be expired (responding to it resumes the run).
    manager.store.create_session(session_id="s-running-with-question")
    await manager.mark_runtime_running("s-running-with-question", request_id="req-question")
    await manager.merge_metadata(
        "s-running-with-question",
        {"pending_question_request": {"question": "Proceed?", "call_id": "call-2"}},
    )

    interrupted = await manager.mark_interrupted_running_sessions(reason="runtime_restarted")
    assert interrupted == 1

    running_session = await manager.get_session("s-running")
    metadata = running_session["metadata"]
    assert metadata["last_runtime_status"] == "interrupted"
    assert metadata["latest_event_state"] == "error"
    assert metadata["completion_state"] == "error"
    assert metadata["last_interrupted_reason"] == "runtime_restarted"

    completed_session = await manager.get_session("s-completed")
    assert completed_session["metadata"]["last_runtime_status"] == "success"

    blocked_session = await manager.get_session("s-blocked-permission")
    blocked_metadata = blocked_session["metadata"]
    assert blocked_metadata["last_runtime_status"] == "permission_requested"
    assert blocked_metadata["pending_permission_request"]["call_id"] == "call-1"
    assert blocked_metadata.get("last_interrupted_reason") is None

    question_session = await manager.get_session("s-running-with-question")
    question_metadata = question_session["metadata"]
    assert question_metadata["last_runtime_status"] == "running"
    assert question_metadata.get("last_interrupted_reason") is None

    # Idempotent: second sweep finds nothing to mark.
    assert await manager.mark_interrupted_running_sessions() == 0


@pytest.mark.asyncio
async def test_chat_run_status_reports_interrupted_session_as_terminal(monkeypatch):
    session_id = "s-interrupted-status"
    request_id = "req-interrupted-status"
    runtime_api.chat_run_registry._records.pop(request_id, None)

    async def _fake_get_existing_session(_session_id):
        return {
            "metadata": {
                "last_execution_id": request_id,
                "last_runtime_status": "interrupted",
                "latest_event_state": "error",
                "last_runtime_updated_at": "2026-07-05T00:00:00Z",
            }
        }

    monkeypatch.setattr(runtime_api.session_manager, "get_existing_session", _fake_get_existing_session)

    payload = await runtime_api._chat_run_status_payload(session_id, request_id)
    assert payload["source_of_truth"] == "session_metadata"
    assert payload["state"] == "failed"
    assert payload["terminal"] is True


@pytest.mark.asyncio
async def test_api_chat_conflicts_on_active_duplicate_request_id(monkeypatch):
    request_id = "portal-chat-dedupe-req-1"
    session_id = "s-chat-dedupe"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)

    runtime_api.chat_run_registry.start(session_id=session_id, request_id=request_id)
    try:
        response = await runtime_api.api_chat(
            _PortalRequest({"message": "same request again", "session_id": session_id, "client_request_id": request_id})
        )
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    assert response.status == 409
    assert b"duplicate_chat_request_id" in response.body


@pytest.mark.asyncio
async def test_api_chat_replays_final_payload_for_completed_duplicate_request_id(monkeypatch):
    request_id = "portal-chat-dedupe-req-2"
    session_id = "s-chat-dedupe-2"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)

    runtime_api.chat_run_registry.start(session_id=session_id, request_id=request_id)
    runtime_api.chat_run_registry.complete(
        request_id,
        {"response": "already answered", "session_id": session_id, "request_id": request_id, "status": "completed"},
    )
    try:
        response = await runtime_api.api_chat(
            _PortalRequest({"message": "same request again", "session_id": session_id, "client_request_id": request_id})
        )
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    assert response.status == 200
    assert b"already answered" in response.body
