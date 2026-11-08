"""Stopping a chat run through the gateway.

The registry's cooperative stop and forced fallback, the cancel endpoint's
two modes, one run per session (409 ``session_busy`` or interrupt-and-send),
the bookkeeping a stop leaves behind (session metadata, usage, final event)
and the projection of a cancelled run as cancelled rather than failed.
"""

import asyncio
import json

import pytest

from src.efp_runtime.events import RuntimeEvent
from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.efp_runtime.session.gateway_facade import RuntimeSessionManager
from src.gateway import runtime_api, runtime_chat
from src.gateway.chat_run_registry import ChatRunRegistry
from src.gateway.runtime_event_projection import RuntimeEventProjector


# ---------------------------------------------------------------- registry


def test_request_cancel_tells_the_run_and_marks_it_cancelling():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    asked: list[str] = []
    registry.register_canceller("r1", lambda: asked.append("first"))

    record = registry.request_cancel("r1")

    assert asked == ["first"]
    assert record.state == "cancelling"
    assert record.terminal is False
    assert record.cancel_requested is True
    assert record.cancel_mode == "cooperative"
    # A run that registers after the stop was asked for hears about it at once.
    registry.register_canceller("r1", lambda: asked.append("late"))
    assert asked == ["first", "late"]
    payload = record.to_payload()
    assert payload["cancel_requested"] is True
    assert payload["cancel_mode"] == "cooperative"


def test_complete_after_a_stop_is_cancelled_only_when_the_payload_says_so():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="stopped")
    registry.request_cancel("stopped")
    registry.complete("stopped", {"response": "part", "status": "cancelled", "completion_state": "cancelled"})
    assert registry.get("stopped").state == "cancelled"
    assert registry.get("stopped").final_payload["response"] == "part"

    # The answer landed just as the stop arrived: it completed.
    registry.start(session_id="s1", request_id="raced")
    registry.request_cancel("raced")
    registry.complete("raced", {"response": "whole answer"})
    assert registry.get("raced").state == "completed"


def test_fail_after_a_stop_records_a_cancelled_run_with_the_error_kept():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")
    registry.request_cancel("r1")

    registry.fail("r1", {"error": "socket closed while stopping"})

    record = registry.get("r1")
    assert record.state == "cancelled"
    assert record.error_payload["error"] == "socket closed while stopping"
    assert record.terminal is True


def test_forced_cancel_cancels_the_task_and_is_final():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")

    async def scenario():
        blocker = asyncio.Event()
        task = asyncio.create_task(blocker.wait())
        registry.attach_task("r1", task)
        assert registry.cancel("r1") is True
        await asyncio.sleep(0)
        assert task.cancelled()
        record = registry.get("r1")
        assert record.state == "cancelled"
        assert record.cancel_mode == "forced"
        # Nothing reopens a cancelled record.
        registry.fail("r1", {"error": "late"})
        assert registry.get("r1").state == "cancelled"
        assert registry.cancel("r1") is False

    asyncio.run(scenario())


def test_active_for_session_reports_the_run_in_flight_and_drops_lost_ones():
    registry = ChatRunRegistry()
    assert registry.active_for_session("s1") is None
    registry.start(session_id="s1", request_id="r1")
    assert registry.active_for_session("s1").request_id == "r1"
    assert registry.active_for_session("other") is None

    registry.complete("r1", {"response": "done"})
    assert registry.active_for_session("s1") is None

    async def scenario():
        registry.start(session_id="s1", request_id="lost")
        task = asyncio.create_task(asyncio.sleep(0))
        registry.attach_task("lost", task)
        await task
        # The producing task ended without a terminal outcome: not active, and
        # finalized so it cannot block the session.
        assert registry.active_for_session("s1") is None
        lost = registry.get("lost")
        assert lost.state == "failed"
        assert lost.error_payload["error"] == "chat_run_lost"

    asyncio.run(scenario())


def test_wait_terminal_returns_when_the_run_settles_or_the_timeout_passes():
    registry = ChatRunRegistry()
    registry.start(session_id="s1", request_id="r1")

    async def scenario():
        assert await registry.wait_terminal("r1", 0.1) is False

        async def settle_soon():
            await asyncio.sleep(0.05)
            registry.complete("r1", {"response": "done"})

        asyncio.create_task(settle_soon())
        assert await registry.wait_terminal("r1", 2) is True
        assert await registry.wait_terminal("missing", 0) is True

    asyncio.run(scenario())


# ------------------------------------------------------------- cancel endpoint


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


class _CancelRequest:
    def __init__(self, request_id, session_id="", grace=None):
        self.match_info = {"request_id": request_id}
        self.query = {"session_id": session_id}
        if grace is not None:
            self.query["grace"] = grace


def _patch_stream_harness(monkeypatch, fake_run, usage_records):
    monkeypatch.setattr(runtime_api, "_run_chat_via_execution_bus", fake_run)
    monkeypatch.setattr(runtime_api, "_emit_gateway_runtime_event", lambda _payload: asyncio.sleep(0, result=None))
    monkeypatch.setattr(runtime_api, "publish_session_metadata", lambda **_kwargs: asyncio.sleep(0, result=True))
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _request: ("agent-1", "Agent One"))
    monkeypatch.setattr(runtime_api.session_manager, "_initialized", True)
    monkeypatch.setattr(runtime_api.session_manager, "mark_runtime_running", lambda *_args, **_kwargs: asyncio.sleep(0, result=None))
    monkeypatch.setattr(runtime_api.web, "StreamResponse", _RecordingStreamResponse)
    monkeypatch.setattr(
        runtime_api,
        "_record_chat_usage",
        lambda usage, **kwargs: usage_records.append({"usage": dict(usage), **kwargs}),
    )
    monkeypatch.setattr(
        runtime_api.global_config,
        "_config",
        {"llm": {"api_key": "k", "model": "gpt-5-mini", "provider": "openai"}},
        raising=False,
    )


def _sse_payloads(response, event_name):
    payloads = []
    for chunk in response.writes:
        if not chunk.startswith(f"event: {event_name}\n"):
            continue
        for line in chunk.split("\n"):
            if line.startswith("data: "):
                payloads.append(json.loads(line[len("data: "):]))
    return payloads


def _record_attr(request_id, name, default=None):
    record = runtime_api.chat_run_registry.get(request_id)
    return getattr(record, name, default) if record is not None else default


async def _wait_for(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


def _cooperative_fake_run(seen):
    async def fake_run(**kwargs):
        seen.append(kwargs)
        stop = asyncio.Event()
        kwargs["register_canceller"](stop.set)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            return {"response": "finished on its own", "usage": {}}
        return {
            "response": "the first words",
            "status": "cancelled",
            "completion_state": "cancelled",
            "cancel_reason": "user_cancelled",
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }

    return fake_run


@pytest.mark.asyncio
async def test_cancel_endpoint_stops_the_run_cooperatively_and_the_stream_ends_cancelled(monkeypatch):
    request_id = "portal-cancel-coop-1"
    session_id = "s-cancel-coop"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    usage_records = []
    seen = []
    _patch_stream_harness(monkeypatch, _cooperative_fake_run(seen), usage_records)

    try:
        stream_task = asyncio.create_task(
            runtime_api.api_chat_stream(
                _PortalRequest({"message": "long task", "session_id": session_id, "client_request_id": request_id})
            )
        )
        await _wait_for(lambda: bool(_record_attr(request_id, "cancellers")))

        cancel_response = await runtime_api.api_chat_run_cancel(_CancelRequest(request_id, session_id))
        response = await asyncio.wait_for(stream_task, timeout=5)
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    cancel_payload = json.loads(cancel_response.body)
    assert cancel_payload["ok"] is True
    assert cancel_payload["cancel_mode"] == "cooperative"
    assert cancel_payload["state"] == "cancelled"
    assert cancel_payload["settled"] is True

    finals = _sse_payloads(response, "final")
    assert finals and finals[0]["completion_state"] == "cancelled"
    assert finals[0]["response"] == "the first words"
    assert finals[0]["status"] == "cancelled"
    assert _sse_payloads(response, "done")
    # The tokens the stopped turn used are booked.
    assert usage_records and usage_records[0]["usage"]["input_tokens"] == 5


@pytest.mark.asyncio
async def test_cancel_endpoint_forces_a_run_that_ignores_the_stop_and_books_its_usage(monkeypatch):
    request_id = "portal-cancel-forced-1"
    session_id = "s-cancel-forced"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    usage_records = []

    async def stubborn_run(**kwargs):
        await kwargs["stream_callback"].put(
            {
                "type": "usage.updated",
                "session_id": session_id,
                "payload": {
                    "run_id": "run-1",
                    "iteration": 1,
                    "usage": {"input_tokens": 7, "output_tokens": 2, "estimated_credits": 0.3},
                    "resolved_model": "gpt-5-mini",
                },
            }
        )
        kwargs["register_canceller"](lambda: None)  # heard, ignored
        await asyncio.sleep(30)
        return {"response": "never", "usage": {}}

    _patch_stream_harness(monkeypatch, stubborn_run, usage_records)

    try:
        stream_task = asyncio.create_task(
            runtime_api.api_chat_stream(
                _PortalRequest({"message": "long task", "session_id": session_id, "client_request_id": request_id})
            )
        )
        await _wait_for(lambda: _record_attr(request_id, "latest_event_seq", 0) >= 1)

        cancel_response = await runtime_api.api_chat_run_cancel(_CancelRequest(request_id, session_id, grace="0.1"))
        response = await asyncio.wait_for(stream_task, timeout=5)
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)

    cancel_payload = json.loads(cancel_response.body)
    assert cancel_payload["ok"] is True
    assert cancel_payload["cancel_mode"] == "forced"
    assert cancel_payload["state"] == "cancelled"

    finals = _sse_payloads(response, "final")
    assert finals and finals[0]["completion_state"] == "cancelled"
    assert finals[0]["cancel_reason"] == "user_cancelled"
    assert _sse_payloads(response, "done")
    # Booked from the run's last usage.updated, since it never returned.
    assert usage_records and usage_records[0]["usage"]["input_tokens"] == 7
    assert usage_records[0]["usage"]["pricing"]["model"] == "gpt-5-mini"


@pytest.mark.asyncio
async def test_cancel_endpoint_reports_a_settled_run_without_touching_it():
    request_id = "portal-cancel-settled-1"
    runtime_api.chat_run_registry._records.pop(request_id, None)
    runtime_api.chat_run_registry.start(session_id="s-settled", request_id=request_id)
    runtime_api.chat_run_registry.complete(request_id, {"response": "done"})
    try:
        response = await runtime_api.api_chat_run_cancel(_CancelRequest(request_id, "s-settled"))
    finally:
        runtime_api.chat_run_registry._records.pop(request_id, None)
    payload = json.loads(response.body)
    assert payload["ok"] is False
    assert payload["state"] == "completed"
    assert payload["settled"] is True


@pytest.mark.asyncio
async def test_cancel_endpoint_unknown_run_is_404():
    response = await runtime_api.api_chat_run_cancel(_CancelRequest("nope", "s"))
    assert response.status == 404


def test_cancel_grace_seconds_reads_the_query_then_the_setting_and_clamps(monkeypatch):
    monkeypatch.delenv("EFP_CHAT_CANCEL_GRACE_SECONDS", raising=False)
    assert runtime_api._chat_cancel_grace_seconds() == 3.0
    assert runtime_api._chat_cancel_grace_seconds("0") == 0.0
    assert runtime_api._chat_cancel_grace_seconds("100") == 30.0
    assert runtime_api._chat_cancel_grace_seconds("abc") == 3.0
    monkeypatch.setenv("EFP_CHAT_CANCEL_GRACE_SECONDS", "1.5")
    assert runtime_api._chat_cancel_grace_seconds() == 1.5
    assert runtime_api._chat_cancel_grace_seconds("2") == 2.0


# ---------------------------------------------------------- one run per session


@pytest.mark.asyncio
async def test_second_message_while_a_run_is_active_is_refused_with_session_busy(monkeypatch):
    session_id = "s-busy"
    first_id = "portal-busy-first"
    second_id = "portal-busy-second"
    for request_id in (first_id, second_id):
        runtime_api.chat_run_registry._records.pop(request_id, None)
    usage_records = []
    seen = []
    _patch_stream_harness(monkeypatch, _cooperative_fake_run(seen), usage_records)

    try:
        first_task = asyncio.create_task(
            runtime_api.api_chat_stream(
                _PortalRequest({"message": "first", "session_id": session_id, "client_request_id": first_id})
            )
        )
        await _wait_for(lambda: runtime_api.chat_run_registry.active_for_session(session_id) is not None)

        refused = await runtime_api.api_chat_stream(
            _PortalRequest({"message": "second", "session_id": session_id, "client_request_id": second_id})
        )
        assert refused.status == 409
        body = json.loads(refused.body)
        assert body["error"] == "session_busy"
        assert body["active_request_id"] == first_id
        assert body["request_id"] == second_id
        assert runtime_api.chat_run_registry.get(second_id) is None

        await runtime_api.api_chat_run_cancel(_CancelRequest(first_id, session_id))
        await asyncio.wait_for(first_task, timeout=5)
    finally:
        for request_id in (first_id, second_id):
            runtime_api.chat_run_registry._records.pop(request_id, None)


@pytest.mark.asyncio
async def test_interrupt_in_flight_run_stops_the_first_run_before_the_second_starts(monkeypatch):
    session_id = "s-interrupt"
    first_id = "portal-interrupt-first"
    second_id = "portal-interrupt-second"
    for request_id in (first_id, second_id):
        runtime_api.chat_run_registry._records.pop(request_id, None)
    monkeypatch.setenv("EFP_CHAT_CANCEL_GRACE_SECONDS", "3")
    usage_records = []
    seen = []
    _patch_stream_harness(monkeypatch, _cooperative_fake_run(seen), usage_records)

    try:
        first_task = asyncio.create_task(
            runtime_api.api_chat_stream(
                _PortalRequest({"message": "first", "session_id": session_id, "client_request_id": first_id})
            )
        )
        await _wait_for(lambda: bool(_record_attr(first_id, "cancellers")))

        second_task = asyncio.create_task(
            runtime_api.api_chat_stream(
                _PortalRequest(
                    {
                        "message": "send now",
                        "session_id": session_id,
                        "client_request_id": second_id,
                        "interrupt_in_flight_run": True,
                    }
                )
            )
        )
        first_response = await asyncio.wait_for(first_task, timeout=5)
        # The second run only starts once the first is gone; its own stop
        # (nobody asks for one) times out into a completed answer.
        await _wait_for(lambda: bool(_record_attr(second_id, "cancellers")))
        await runtime_api.api_chat_run_cancel(_CancelRequest(second_id, session_id))
        second_response = await asyncio.wait_for(second_task, timeout=10)
    finally:
        for request_id in (first_id, second_id):
            runtime_api.chat_run_registry._records.pop(request_id, None)

    assert _sse_payloads(first_response, "final")[0]["completion_state"] == "cancelled"
    assert _sse_payloads(second_response, "final")
    assert [kwargs["message"] for kwargs in seen] == ["first", "send now"]


# ------------------------------------------------------------------ bookkeeping


@pytest.mark.asyncio
async def test_record_runtime_cancelled_leaves_the_session_in_a_terminal_state(tmp_path):
    manager = RuntimeSessionManager(root=tmp_path)
    manager.store.create_session(session_id="s-cancelled")
    await manager.mark_runtime_running("s-cancelled", request_id="req-1")

    await manager.record_runtime_cancelled("s-cancelled", request_id="req-1")

    metadata = (await manager.get_session("s-cancelled"))["metadata"]
    assert metadata["last_runtime_status"] == "cancelled"
    assert metadata["latest_event_type"] == "chat.cancelled"
    assert metadata["latest_event_state"] == "cancelled"
    assert metadata["completion_state"] == "cancelled"
    assert metadata["last_execution_id"] == "req-1"
    assert "pending_tool_calls" not in metadata


def test_result_payload_marks_a_cancelled_run_as_cancelled():
    result = RuntimeLoopResult(
        session_id="s-1",
        final_assistant_message=None,
        iterations=1,
        status=LoopStatus.CANCELLED,
        runtime_events=[],
        usage={"input_tokens": 1},
    )

    payload = runtime_chat._result_payload(result, request_id="req-1", model="gpt-5-mini")

    assert payload["status"] == "cancelled"
    assert payload["completion_state"] == "cancelled"
    assert payload["cancel_reason"] == "user_cancelled"
    assert payload["response"] == ""


def test_completed_result_payload_has_no_cancel_fields():
    result = RuntimeLoopResult(
        session_id="s-1",
        final_assistant_message=None,
        iterations=1,
        status=LoopStatus.COMPLETED,
        runtime_events=[],
        usage={},
    )

    payload = runtime_chat._result_payload(result, request_id="req-1", model="gpt-5-mini")

    assert "completion_state" not in payload
    assert "cancel_reason" not in payload


@pytest.mark.asyncio
async def test_chat_run_status_reports_cancelled_session_metadata_as_terminal(monkeypatch):
    session_id = "s-cancelled-status"
    request_id = "req-cancelled-status"
    runtime_api.chat_run_registry._records.pop(request_id, None)

    async def _fake_get_existing_session(_session_id):
        return {
            "metadata": {
                "last_execution_id": request_id,
                "last_runtime_status": "cancelled",
                "latest_event_state": "cancelled",
                "last_runtime_updated_at": "2026-11-07T00:00:00Z",
            }
        }

    monkeypatch.setattr(runtime_api.session_manager, "get_existing_session", _fake_get_existing_session)

    payload = await runtime_api._chat_run_status_payload(session_id, request_id)
    assert payload["state"] == "cancelled"
    assert payload["terminal"] is True


# ------------------------------------------------------------------- projection


def test_projector_maps_a_cancelled_run_to_its_own_lifecycle_type_not_a_failure():
    projector = RuntimeEventProjector(request_id="req-1", agent_id="agent-1")

    cancelled = projector.project(
        RuntimeEvent(
            type="run_cancelled",
            message="Runtime run was cancelled.",
            session_id="s-1",
            payload={"run_id": "run-1", "phase": "provider_stream", "iterations": 1},
            created_at="2026-11-07T01:02:03Z",
        )
    )[-1]
    assert cancelled["type"] == "session.next.step.cancelled"
    assert cancelled["state"] == "cancelled"
    assert cancelled["data"]["phase"] == "provider_stream"

    finish = projector.project(
        RuntimeEvent(
            type="run_finish",
            session_id="s-1",
            payload={"status": "cancelled", "iterations": 1, "usage": {"total_tokens": 4}},
            created_at="2026-11-07T01:02:04Z",
        )
    )[-1]
    assert finish["type"] == "session.next.step.cancelled"
    assert finish["state"] == "cancelled"
    assert finish["data"]["tokens"] == 4

    failed = projector.project(
        RuntimeEvent(
            type="run_finish",
            session_id="s-1",
            payload={"status": "error", "iterations": 1},
            created_at="2026-11-07T01:02:05Z",
        )
    )[-1]
    assert failed["type"] == "session.next.step.failed"
    assert failed["state"] == "failed"
