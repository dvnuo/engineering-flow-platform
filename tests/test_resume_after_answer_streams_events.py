"""A run started by an answer has to feed viewers the way a sent run does.

Answering a card resumes the run with nobody attached, and it was resumed
with no stream callback. With no callback nothing subscribed to the run's
events, nothing projected them, and nothing handed them to the gateway bus
or the run registry. Portal follows that run by request id over the events
socket -- which drops every event not carrying the id -- and the reconnect
stream can only replay what the registry recorded. A joined viewer saw an
empty spinner; when the run stopped on a second question, the
question.requested event that raises the card never arrived, and only a
reload showed it.
"""
from __future__ import annotations

import asyncio
import types

import pytest



@pytest.fixture()
def api():
    from src.gateway import runtime_api

    return runtime_api


def _projected(event_type: str, request_id: str, data: dict) -> dict:
    return {"type": event_type, "event_type": event_type, "request_id": request_id, "data": data, "properties": {}}


def test_a_resumed_run_hands_its_events_to_the_bus_and_the_registry(api, monkeypatch):
    emitted: list[tuple[str, dict]] = []

    async def fake_emit(event_type, payload):
        emitted.append((event_type, payload))

    async def fake_resume(**kwargs):
        # The run raises a follow-up question part-way through.
        queue = kwargs["stream_callback"]
        rid = kwargs["request_id"]
        await queue.put(_projected("session.next.step.started", rid, {"run_id": "r2"}))
        await queue.put(_projected("question.requested", rid, {"question_request": {"request_id": "q-2"}}))
        return {"status": "waiting_for_question", "pending_question_request": {"request_id": "q-2"}, "response": ""}

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(api, "emit_agent_event", fake_emit)
    monkeypatch.setattr(api, "resume_runtime_chat", fake_resume)
    monkeypatch.setattr(api, "global_config", types.SimpleNamespace(llm={"model": "test-model"}))
    monkeypatch.setattr(api, "session_manager", types.SimpleNamespace(mark_runtime_running=noop))
    monkeypatch.setattr(api, "_resolve_runtime_agent_identity", lambda _r: ("agent-1", "Agent"))

    async def run():
        started = await api._resume_chat_after_user_input(
            object(), session_id="s1", execution_metadata={}
        )
        # The resume runs detached; wait for it.
        await asyncio.gather(*list(api._RESUME_TASKS))
        return started

    started = asyncio.run(run())
    rid = started["request_id"]

    # Every event reached the gateway bus, stamped with the run it belongs to
    # -- which is what the socket filter keys on.
    assert [e[0] for e in emitted] == ["session.next.step.started", "question.requested"]
    assert all(e[1]["request_id"] == rid for e in emitted)

    # And the registry saw them before it was told the run had finished.
    record = api.chat_run_registry.get(rid)
    assert record is not None
    assert record.latest_event_seq == 2
    assert record.state == "completed"
    assert record.final_payload["status"] == "waiting_for_question"


def test_a_resumed_run_that_fails_still_stops_draining(api, monkeypatch):
    async def fake_emit(*_a, **_k):
        return None

    async def fake_resume(**kwargs):
        raise RuntimeError("provider down")

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(api, "emit_agent_event", fake_emit)
    monkeypatch.setattr(api, "resume_runtime_chat", fake_resume)
    monkeypatch.setattr(api, "global_config", types.SimpleNamespace(llm={"model": "test-model"}))
    monkeypatch.setattr(api, "session_manager", types.SimpleNamespace(mark_runtime_running=noop))
    monkeypatch.setattr(api, "_resolve_runtime_agent_identity", lambda _r: ("agent-1", "Agent"))
    monkeypatch.setattr(api, "_persist_chat_failure_state", noop)

    async def run():
        started = await api._resume_chat_after_user_input(
            object(), session_id="s2", execution_metadata={}
        )
        await asyncio.wait_for(asyncio.gather(*list(api._RESUME_TASKS)), timeout=5)
        return started

    started = asyncio.run(run())
    record = api.chat_run_registry.get(started["request_id"])
    assert record is not None and record.state == "failed"
