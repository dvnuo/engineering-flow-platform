import json
from types import SimpleNamespace

import pytest

from tests._import_helpers import load_module_from_repo_path

load_module_from_repo_path("src.gateway.event_bus", "src/gateway/event_bus.py")
events = load_module_from_repo_path("src.gateway.events", "src/gateway/events.py")


class _FakeWS:
    def __init__(self):
        self.closed = False
        self.sent = []

    async def prepare(self, request):
        return self

    async def send_str(self, _data):
        self.sent.append(_data)
        return None

    def exception(self):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


@pytest.mark.asyncio
async def test_handle_websocket_passes_query_filters_to_event_bus(monkeypatch):
    captured = {}

    async def _fake_add_listener(queue, filters=None):
        captured["queue"] = queue
        captured["filters"] = filters

    async def _fake_remove_listener(_queue):
        captured["removed"] = True

    monkeypatch.setattr(events.web, "WebSocketResponse", _FakeWS)
    monkeypatch.setattr(events.event_bus, "add_listener", _fake_add_listener)
    monkeypatch.setattr(events.event_bus, "remove_listener", _fake_remove_listener)
    monkeypatch.setattr(events.event_bus, "_listeners", [])

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={
                "session_id": " s-1 ",
                "task_id": "t-1",
                "group_id": "",
                "coordination_run_id": "coord-1",
                "agent_id": "agent-1",
                "request_id": " req-1 ",
            }
        )
    )

    ws = await events.handle_websocket(request)

    assert isinstance(ws, _FakeWS)
    assert captured["filters"] == {
        "session_id": "s-1",
        "task_id": "t-1",
        "coordination_run_id": "coord-1",
        "agent_id": "agent-1",
        "request_id": "req-1",
    }
    assert captured.get("removed") is True


@pytest.mark.asyncio
async def test_handle_websocket_passes_combined_session_and_request_filters_and_omits_blank_values(monkeypatch):
    captured = {}

    async def _fake_add_listener(queue, filters=None):
        captured["queue"] = queue
        captured["filters"] = filters

    async def _fake_remove_listener(_queue):
        captured["removed"] = True

    monkeypatch.setattr(events.web, "WebSocketResponse", _FakeWS)
    monkeypatch.setattr(events.event_bus, "add_listener", _fake_add_listener)
    monkeypatch.setattr(events.event_bus, "remove_listener", _fake_remove_listener)
    monkeypatch.setattr(events.event_bus, "_listeners", [])

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={
                "session_id": " s-keep ",
                "request_id": " req-keep ",
                "task_id": " ",
            }
        )
    )

    ws = await events.handle_websocket(request)

    assert isinstance(ws, _FakeWS)
    assert captured["filters"] == {
        "session_id": "s-keep",
        "request_id": "req-keep",
    }
    assert captured.get("removed") is True


@pytest.mark.asyncio
async def test_handle_websocket_replays_matching_events_after_connected(monkeypatch):
    captured = {}

    async def _fake_add_listener(queue, filters=None):
        captured["queue"] = queue
        captured["filters"] = filters

    async def _fake_remove_listener(_queue):
        captured["removed"] = True

    async def _fake_replay_events(**kwargs):
        captured["replay"] = kwargs
        return [
            '{"type":"session.next.text.delta","data":{"session_id":"s-1","request_id":"req-1","delta":"hi"},"ts":1}'
        ]

    monkeypatch.setattr(events.web, "WebSocketResponse", _FakeWS)
    monkeypatch.setattr(events.event_bus, "add_listener", _fake_add_listener)
    monkeypatch.setattr(events.event_bus, "remove_listener", _fake_remove_listener)
    monkeypatch.setattr(events.event_bus, "replay_events", _fake_replay_events)
    monkeypatch.setattr(events.event_bus, "_listeners", [])

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={
                "session_id": "s-1",
                "request_id": "req-1",
                "replay": "1",
                "replay_limit": "5",
                "last_event_at": "2026-06-02T01:00:00Z",
            }
        )
    )

    ws = await events.handle_websocket(request)

    assert json.loads(ws.sent[0])["type"] == "connected"
    replayed = json.loads(ws.sent[1])
    assert replayed["type"] == "session.next.text.delta"
    assert replayed["data"]["request_id"] == "req-1"
    assert captured["replay"] == {
        "filters": {"session_id": "s-1", "request_id": "req-1"},
        "replay_limit": 5,
        "last_event_at": "2026-06-02T01:00:00Z",
    }
    assert captured.get("removed") is True
