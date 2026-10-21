"""The session name reaches the Portal: with every publish, and on its own after generation or a rename."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.runtime import portal_session_metadata_client as client


def test_session_title_is_a_control_plane_key():
    payload = client.build_session_metadata_payload(
        last_execution_id="exec-1",
        latest_event_type="chat.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={"session_title": "Release pipeline permissions", "unknown": "drop-me"},
    )
    assert json.loads(payload["metadata_json"]) == {"session_title": "Release pipeline permissions"}


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    async def text(self):
        return "nope"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeClientSession:
    calls = []
    status = 200

    def __init__(self, headers=None, timeout=None):
        self.headers = headers
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def _call(self, method, url, json=None):
        _FakeClientSession.calls.append({"method": method, "url": url, "json": json, "headers": self.headers, "timeout": self.timeout})
        return _FakeResponse(_FakeClientSession.status)

    def patch(self, url, json=None):
        return self._call("patch", url, json=json)

    def put(self, url, json=None):
        return self._call("put", url, json=json)


@pytest.fixture
def portal(monkeypatch):
    _FakeClientSession.calls = []
    _FakeClientSession.status = 200
    monkeypatch.setattr(client, "ClientSession", _FakeClientSession)
    monkeypatch.setattr(client, "get_portal_internal_base_url", lambda: "http://portal.internal")
    return _FakeClientSession


async def test_publish_session_title_patches_the_portal_row(portal):
    assert await client.publish_session_title(agent_id="agent-1", session_id="s-1", title="  Release pipeline permissions ") is True

    assert len(portal.calls) == 1
    call = portal.calls[0]
    assert call["method"] == "patch"
    assert call["url"] == "http://portal.internal/api/internal/agents/agent-1/sessions/s-1/title"
    assert call["json"] == {"title": "Release pipeline permissions"}
    assert call["headers"] == {"Content-Type": "application/json"}
    assert call["timeout"].total == client._TITLE_PUBLISH_TIMEOUT_SECONDS


async def test_publish_session_metadata_still_puts_the_full_row(portal):
    await client.publish_session_metadata(
        agent_id="agent-1",
        session_id="s-1",
        last_execution_id="exec-1",
        latest_event_type="chat.completed",
        latest_event_state="success",
        snapshot_version=None,
        runtime_events=[],
        metadata={"session_title": "Release pipeline permissions"},
    )
    call = portal.calls[0]
    assert call["method"] == "put"
    assert call["url"] == "http://portal.internal/api/internal/agents/agent-1/sessions/s-1/metadata"
    assert json.loads(call["json"]["metadata_json"]) == {"session_title": "Release pipeline permissions"}
    assert call["timeout"] is None


async def test_publish_session_title_is_quiet_about_failures(portal, caplog):
    portal.status = 404
    assert await client.publish_session_title(agent_id="agent-1", session_id="s-1", title="x") is False  # no exception
    assert len(portal.calls) == 1


@pytest.mark.parametrize("agent_id, session_id, title", [("", "s-1", "x"), ("a", "", "x"), ("a", "s-1", "  ")])
async def test_publish_session_title_skips_incomplete_input(portal, agent_id, session_id, title):
    assert await client.publish_session_title(agent_id=agent_id, session_id=session_id, title=title) is False
    assert portal.calls == []


async def test_publish_session_title_needs_a_portal(monkeypatch, portal):
    monkeypatch.setattr(client, "get_portal_internal_base_url", lambda: "")
    assert await client.publish_session_title(agent_id="a", session_id="s-1", title="x") is False
    assert portal.calls == []


async def test_publish_session_title_in_background_keeps_the_task_until_done(portal):
    task = client.publish_session_title_in_background(agent_id="agent-1", session_id="s-1", title="Name")
    assert task is not None
    assert task in client._background_publishes
    assert await task is True
    await asyncio.sleep(0)
    assert task not in client._background_publishes
    assert portal.calls[0]["json"] == {"title": "Name"}


def test_publish_session_title_in_background_without_a_loop_is_a_no_op():
    assert client.publish_session_title_in_background(agent_id="agent-1", session_id="s-1", title="Name") is None
