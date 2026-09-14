"""Gateway tests for the connectors respond endpoint and event projection."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.efp_runtime.connector_bridge import ConnectorBridgeBroker, get_connector_bridge_broker
from src.efp_runtime.events import RuntimeEvent
from src.gateway import runtime_api
from src.gateway.runtime_chat import _execution_metadata_enables_browser_tool
from src.gateway.runtime_event_projection import is_projected_runtime_event, project_runtime_event


class _Request:
    headers: dict = {}

    def __init__(self, session_id: str, body, *, content_length: int | None = 64):
        self.match_info = {"session_id": session_id}
        self._body = body
        self.content_length = content_length

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _broker() -> ConnectorBridgeBroker:
    broker = get_connector_bridge_broker()
    broker.clear()
    return broker


@pytest.mark.asyncio
async def test_connectors_respond_resolves_pending_request():
    broker = _broker()
    request = broker.create(session_id="sess-1", connector_type="local_browser", action="tab.list")
    waiter = asyncio.create_task(broker.wait(request.request_id))
    await asyncio.sleep(0)

    response = await runtime_api.api_session_connectors_respond(
        _Request("sess-1", {"request_id": request.request_id, "client_id": "tab-1", "ok": True, "result": {"tabs": []}})
    )
    assert response.status == 202
    body = json.loads(response.text)
    assert body == {"ok": True, "session_id": "sess-1", "request_id": request.request_id}

    payload = await waiter
    assert payload["ok"] is True
    assert payload["result"] == {"tabs": []}
    assert payload["client_id"] == "tab-1"
    assert payload["error"] is None


@pytest.mark.asyncio
async def test_connectors_respond_failure_payload_carries_error():
    broker = _broker()
    request = broker.create(session_id="sess-2", connector_type="local_browser", action="page.click")
    waiter = asyncio.create_task(broker.wait(request.request_id))
    await asyncio.sleep(0)
    response = await runtime_api.api_session_connectors_respond(
        _Request("sess-2", {"request_id": request.request_id, "ok": False, "error": {"code": "session_busy", "message": "busy"}})
    )
    assert response.status == 202
    payload = await waiter
    assert payload["ok"] is False
    assert payload["error"]["code"] == "session_busy"


@pytest.mark.asyncio
async def test_connectors_respond_rejects_unknown_or_foreign_requests():
    broker = _broker()
    request = broker.create(session_id="sess-3", connector_type="local_browser", action="tab.list")
    waiter = asyncio.create_task(broker.wait(request.request_id))
    await asyncio.sleep(0)

    unknown = await runtime_api.api_session_connectors_respond(_Request("sess-3", {"request_id": "cr_nope", "ok": True}))
    assert unknown.status == 409
    assert json.loads(unknown.text)["error"] == "connector_request_not_pending"

    foreign = await runtime_api.api_session_connectors_respond(_Request("other", {"request_id": request.request_id, "ok": True}))
    assert foreign.status == 409

    missing = await runtime_api.api_session_connectors_respond(_Request("sess-3", {"ok": True}))
    assert missing.status == 400

    bad_json = await runtime_api.api_session_connectors_respond(
        _Request("sess-3", json.JSONDecodeError("bad", "", 0))
    )
    assert bad_json.status == 400

    too_large = await runtime_api.api_session_connectors_respond(
        _Request("sess-3", {"request_id": request.request_id, "ok": True}, content_length=3 * 1024 * 1024)
    )
    assert too_large.status == 413

    not_object = await runtime_api.api_session_connectors_respond(_Request("sess-3", ["x"]))
    assert not_object.status == 400

    assert broker.resolve(request.request_id, {"ok": True, "result": {}})
    await waiter


@pytest.mark.asyncio
async def test_connectors_pending_lists_session_requests():
    broker = _broker()
    request = broker.create(session_id="sess-4", connector_type="local_browser", action="page.snapshot", target_client_id="tab-9")
    response = await runtime_api.api_session_connectors_pending(_Request("sess-4", {}))
    body = json.loads(response.text)
    assert body["session_id"] == "sess-4"
    assert [item["id"] for item in body["requests"]] == [request.request_id]
    assert body["requests"][0]["target_client_id"] == "tab-9"
    other = json.loads((await runtime_api.api_session_connectors_pending(_Request("sess-5", {}))).text)
    assert other["requests"] == []
    broker.clear()


def test_projection_maps_connector_events():
    request_payload = {
        "id": "cr_abc",
        "request_id": "cr_abc",
        "action": "page.snapshot",
        "params": {"target_id": "T1"},
        "target_client_id": "tab-1",
        "timeout_seconds": 60,
        "created_at": "2026-09-13T00:00:00Z",
    }
    requested = RuntimeEvent(
        type="tool.connector_requested",
        message="Browser connector request: page.snapshot",
        session_id="sess-1",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "browser",
            "connector_type": "local_browser",
            "connector_request": request_payload,
            "target_client_id": "tab-1",
            "action": "page.snapshot",
            "arguments": {"action": "page.snapshot", "params": {"target_id": "T1"}},
        },
    )
    projected = project_runtime_event(requested, request_id="chat-1", agent_id="agent-1")
    assert len(projected) == 1
    event = projected[0]
    assert event["type"] == "connector.request"
    assert event["state"] == "pending"
    assert event["data"]["session_id"] == "sess-1"
    assert event["data"]["request_id"] == "chat-1"
    assert event["data"]["connector_type"] == "local_browser"
    assert event["data"]["connector_request"]["id"] == "cr_abc"
    assert event["data"]["connector_request"]["params"] == {"target_id": "T1"}
    assert event["data"]["connector_request_id"] == "cr_abc"
    assert event["data"]["target_client_id"] == "tab-1"
    assert event["data"]["tool_name"] == "browser"
    assert is_projected_runtime_event(event)

    responded = RuntimeEvent(
        type="tool.connector_responded",
        session_id="sess-1",
        payload={
            "tool_call_id": "call-1",
            "tool_name": "browser",
            "connector_type": "local_browser",
            "connector_request_id": "cr_abc",
            "action": "page.snapshot",
            "ok": False,
            "duration_ms": 1200,
            "error_code": "connector_timeout",
        },
    )
    projected = project_runtime_event(responded, request_id="chat-1")
    assert projected[0]["type"] == "connector.responded"
    assert projected[0]["state"] == "error"
    assert projected[0]["data"]["error_code"] == "connector_timeout"
    assert projected[0]["data"]["duration_ms"] == 1200
    assert is_projected_runtime_event(projected[0])


def test_execution_metadata_gate():
    assert _execution_metadata_enables_browser_tool(None) is False
    assert _execution_metadata_enables_browser_tool({}) is False
    assert _execution_metadata_enables_browser_tool({"enable_browser_tool": True}) is True
    assert _execution_metadata_enables_browser_tool({"connectors": {"local_browser": {"client_id": "t"}}}) is True
    assert _execution_metadata_enables_browser_tool({"connectors": {"local_browser": {"enabled": False}}}) is False
    assert _execution_metadata_enables_browser_tool({"connectors": {"other": {"enabled": True}}}) is False
