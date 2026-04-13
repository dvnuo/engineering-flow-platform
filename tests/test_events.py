from types import SimpleNamespace

import pytest

from src.gateway import events


class _FakeWS:
    def __init__(self):
        self.closed = False

    async def prepare(self, request):
        return self

    async def send_str(self, _data):
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
