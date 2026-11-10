import json
from types import SimpleNamespace

import pytest

from tests._import_helpers import load_module_from_repo_path

load_module_from_repo_path("src.gateway.event_bus", "src/gateway/event_bus.py")
events = load_module_from_repo_path("src.gateway.events", "src/gateway/events.py")


class _FakeWS:
    def __init__(self, heartbeat=None):
        self.closed = False
        self.sent = []
        self.heartbeat = heartbeat

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


def _text_delta(text, *, event_id, request_id="req-1"):
    data = {"message_id": "msg-1", "part_id": "part-1", "delta": text, "text": text}
    return {
        "id": event_id,
        "type": "session.next.text.delta",
        "event_type": "session.next.text.delta",
        "session_id": "s-1",
        "request_id": request_id,
        "properties": dict(data),
        "data": dict(data),
    }


def _step(name, *, request_id="req-1"):
    return {
        "id": f"evt-{name}",
        "type": "session.next.step.started",
        "event_type": "session.next.step.started",
        "session_id": "s-1",
        "request_id": request_id,
        "properties": {"step": name},
        "data": {"step": name},
    }


def _bus_message(event):
    """An event as the bus hands it to a listener."""
    return json.dumps({"type": event["type"], "data": event, "ts": 1.0, "created_at": "2026-11-09T00:00:00Z"})


def _run_seqs(messages):
    return [json.loads(message)["data"]["run_seq"] for message in messages]


def _fresh_run_registry(monkeypatch):
    registry = type(events.chat_run_registry)()
    monkeypatch.setattr(events, "chat_run_registry", registry)
    return registry


def _patch_socket(monkeypatch, captured):
    async def _fake_add_listener(queue, filters=None):
        captured["filters"] = filters

    async def _fake_remove_listener(_queue):
        captured["removed"] = True

    async def _fake_replay_events(**kwargs):
        captured["replay"] = kwargs
        return ['{"type":"session.next.text.delta","data":{"session_id":"s-1","delta":"hi"},"ts":1}']

    monkeypatch.setattr(events.web, "WebSocketResponse", _FakeWS)
    monkeypatch.setattr(events.event_bus, "add_listener", _fake_add_listener)
    monkeypatch.setattr(events.event_bus, "remove_listener", _fake_remove_listener)
    monkeypatch.setattr(events.event_bus, "replay_events", _fake_replay_events)
    monkeypatch.setattr(events.event_bus, "_listeners", [])


@pytest.mark.asyncio
async def test_handle_websocket_serves_a_run_from_its_journal_after_the_viewers_sequence_number(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    registry = _fresh_run_registry(monkeypatch)
    registry.start(session_id="s-1", request_id="req-1")
    registry.record_event("req-1", _text_delta("Hel", event_id="e1"))
    registry.record_event("req-1", _text_delta("lo ", event_id="e2"))
    registry.record_event("req-1", _text_delta("world", event_id="e3"))

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={"session_id": "s-1", "request_id": "req-1", "replay": "1", "after_seq": "1"}
        )
    )

    ws = await events.handle_websocket(request)

    connected = json.loads(ws.sent[0])
    assert connected["type"] == "connected"
    assert connected["attach"] == {
        "request_id": "req-1",
        "found": True,
        "state": "running",
        "after_seq": 1,
        "first_seq": 1,
        "last_seq": 3,
    }
    # Everything after the first event, in the shape the bus sends an event in.
    assert len(ws.sent) == 2
    caught_up = json.loads(ws.sent[1])
    assert caught_up["type"] == "session.next.text.delta"
    assert isinstance(caught_up["ts"], float)
    assert caught_up["created_at"].endswith("Z")
    assert caught_up["data"]["id"] == "e3@2-3"
    assert caught_up["data"]["request_id"] == "req-1"
    assert caught_up["data"]["run_seq"] == 3
    assert caught_up["data"]["run_seq_from"] == 2
    assert caught_up["data"]["data"]["delta"] == "lo world"
    assert caught_up["data"]["properties"]["delta"] == "lo world"
    # The journal is the replay: the bus history's tail is not sent on top.
    assert "replay" not in captured
    assert captured.get("removed") is True


@pytest.mark.asyncio
async def test_handle_websocket_tells_the_viewer_when_the_run_it_names_has_no_journal(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    _fresh_run_registry(monkeypatch)

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={"session_id": "s-1", "request_id": "req-9", "replay": "1", "after_seq": "0"}
        )
    )

    ws = await events.handle_websocket(request)

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["attach"] == {
        "request_id": "req-9",
        "found": False,
        "state": "",
        "after_seq": 0,
    }
    assert "replay" not in captured


@pytest.mark.asyncio
async def test_handle_websocket_reports_a_finished_run_and_its_last_events(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    registry = _fresh_run_registry(monkeypatch)
    registry.start(session_id="s-1", request_id="req-1")
    registry.record_event("req-1", _step("one"))
    registry.record_event("req-1", _step("two"))
    registry.complete("req-1", {"ok": True, "response": "done"})

    request = SimpleNamespace(
        rel_url=SimpleNamespace(query={"session_id": "s-1", "request_id": "req-1", "after_seq": "1"})
    )

    ws = await events.handle_websocket(request)

    attach = json.loads(ws.sent[0])["attach"]
    assert attach["found"] is True
    assert attach["state"] == "completed"
    assert attach["last_seq"] == 2
    assert _run_seqs(ws.sent[1:]) == [2]


@pytest.mark.asyncio
async def test_handle_websocket_does_not_attach_a_journal_of_another_session(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    registry = _fresh_run_registry(monkeypatch)
    registry.start(session_id="s-1", request_id="req-1")
    registry.record_event("req-1", _step("one"))

    request = SimpleNamespace(
        rel_url=SimpleNamespace(query={"session_id": "s-other", "request_id": "req-1", "after_seq": "0"})
    )

    ws = await events.handle_websocket(request)

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["attach"]["found"] is False


@pytest.mark.asyncio
async def test_handle_websocket_ignores_a_sequence_number_without_a_run(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    _fresh_run_registry(monkeypatch)

    request = SimpleNamespace(
        rel_url=SimpleNamespace(query={"session_id": "s-1", "replay": "1", "after_seq": "3"})
    )

    ws = await events.handle_websocket(request)

    # A socket that follows a whole session works as it always has.
    assert "attach" not in json.loads(ws.sent[0])
    assert captured["replay"]["filters"] == {"session_id": "s-1"}
    assert len(ws.sent) == 2


@pytest.mark.asyncio
async def test_handle_websocket_ignores_a_sequence_number_it_cannot_read(monkeypatch):
    captured = {}
    _patch_socket(monkeypatch, captured)
    _fresh_run_registry(monkeypatch)

    request = SimpleNamespace(
        rel_url=SimpleNamespace(
            query={"session_id": "s-1", "request_id": "req-1", "replay": "1", "after_seq": "soon"}
        )
    )

    ws = await events.handle_websocket(request)

    assert "attach" not in json.loads(ws.sent[0])
    assert captured["replay"]["filters"] == {"session_id": "s-1", "request_id": "req-1"}


def test_parse_after_seq_reads_only_a_number():
    assert events._parse_after_seq("7") == 7
    assert events._parse_after_seq(" 0 ") == 0
    assert events._parse_after_seq("-4") == 0
    assert events._parse_after_seq(None) is None
    assert events._parse_after_seq("") is None
    assert events._parse_after_seq("soon") is None


def test_run_attach_passes_the_next_event_and_skips_what_it_already_sent(monkeypatch):
    registry = _fresh_run_registry(monkeypatch)
    registry.start(session_id="s-1", request_id="req-1")
    first, second, third = _step("one"), _step("two"), _step("three")
    registry.record_event("req-1", first)
    registry.record_event("req-1", second)

    attach = events._RunAttach("req-1", "s-1", 0)
    _described, catch_up = attach.snapshot()
    assert _run_seqs(catch_up) == [1, 2]

    # Both were also waiting on the socket's queue when the journal was read.
    assert attach.relay(_bus_message(first)) == []
    assert attach.relay(_bus_message(second)) == []

    registry.record_event("req-1", third)
    message = _bus_message(third)
    assert attach.relay(message) == [message]
    assert attach.last_sent_seq == 3


def test_run_attach_follows_a_run_that_starts_after_the_socket_opened(monkeypatch):
    registry = _fresh_run_registry(monkeypatch)
    attach = events._RunAttach("req-1", "s-1", 0)
    described, catch_up = attach.snapshot()
    assert described == {"request_id": "req-1", "found": False, "state": "", "after_seq": 0}
    assert catch_up == []

    registry.start(session_id="s-1", request_id="req-1")
    first, second = _step("one"), _step("two")
    registry.record_event("req-1", first)
    registry.record_event("req-1", second)

    assert attach.relay(_bus_message(first)) == [_bus_message(first)]
    assert attach.relay(_bus_message(second)) == [_bus_message(second)]
    assert attach.last_sent_seq == 2


def test_run_attach_fills_a_gap_from_the_journal(monkeypatch):
    registry = _fresh_run_registry(monkeypatch)
    registry.start(session_id="s-1", request_id="req-1")
    attach = events._RunAttach("req-1", "s-1", 0)
    assert attach.snapshot()[1] == []

    recorded = [_step(f"step-{number}") for number in range(1, 5)]
    for event in recorded:
        registry.record_event("req-1", event)

    # The socket's queue dropped the first three; the fourth is what arrives.
    sent = attach.relay(_bus_message(recorded[3]))

    assert _run_seqs(sent) == [1, 2, 3, 4]
    assert [json.loads(message)["data"]["id"] for message in sent] == [
        "evt-step-1",
        "evt-step-2",
        "evt-step-3",
        "evt-step-4",
    ]
    assert attach.last_sent_seq == 4
    # One the queue still held is not sent a second time.
    assert attach.relay(_bus_message(recorded[2])) == []


def test_run_attach_says_where_it_resumes_when_the_journal_lost_its_start(monkeypatch):
    registry = _fresh_run_registry(monkeypatch)
    record = registry.start(session_id="s-1", request_id="req-1")
    record.journal = type(record.journal)(max_records=2)
    for number in range(1, 6):
        registry.record_event("req-1", _step(f"step-{number}"))

    attach = events._RunAttach("req-1", "s-1", 0)
    described, catch_up = attach.snapshot()

    assert described["first_seq"] == 4
    assert described["last_seq"] == 5
    assert json.loads(catch_up[0]) == {
        "type": "resync",
        "data": {"request_id": "req-1", "reason": "journal_truncated", "first_seq": 4},
    }
    assert _run_seqs(catch_up[1:]) == [4, 5]
    assert attach.last_sent_seq == 5

    # A viewer that already has everything the journal dropped is not told so.
    up_to_date = events._RunAttach("req-1", "s-1", 3)
    assert _run_seqs(up_to_date.snapshot()[1]) == [4, 5]


def test_run_attach_without_a_journal_says_what_it_skipped(monkeypatch):
    _fresh_run_registry(monkeypatch)
    attach = events._RunAttach("req-1", "s-1", 0)
    one = _bus_message(dict(_step("one"), run_seq=1))
    five = _bus_message(dict(_step("five"), run_seq=5))

    assert attach.relay(one) == [one]

    sent = attach.relay(five)
    assert json.loads(sent[0]) == {
        "type": "resync",
        "data": {"request_id": "req-1", "reason": "journal_unavailable", "first_seq": 5},
    }
    assert sent[1:] == [five]
    assert attach.last_sent_seq == 5


def test_run_attach_passes_other_messages_through_untouched(monkeypatch):
    _fresh_run_registry(monkeypatch)
    attach = events._RunAttach("req-1", "s-1", 4)
    unnumbered = json.dumps({"type": "usage.updated", "data": {"request_id": "req-1"}, "ts": 1.0})
    other_run = _bus_message(dict(_step("one", request_id="req-2"), run_seq=9))
    unreadable = '{"run_seq" is mentioned but this is not JSON'

    assert attach.relay(unnumbered) == [unnumbered]
    assert attach.relay(other_run) == [other_run]
    assert attach.relay(unreadable) == [unreadable]
    assert attach.last_sent_seq == 4
