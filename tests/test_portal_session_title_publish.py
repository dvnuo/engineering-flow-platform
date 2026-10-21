"""The session name reaches the Portal: with every publish, and on its own after generation."""

from __future__ import annotations

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

    def __init__(self, headers=None):
        self.headers = headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def patch(self, url, json=None):
        _FakeClientSession.calls.append({"url": url, "json": json, "headers": self.headers})
        return _FakeResponse(_FakeClientSession.status)


@pytest.fixture
def portal(monkeypatch):
    _FakeClientSession.calls = []
    _FakeClientSession.status = 200
    monkeypatch.setattr(client, "ClientSession", _FakeClientSession)
    monkeypatch.setattr(client, "get_portal_internal_base_url", lambda: "http://portal.internal")
    return _FakeClientSession


async def test_publish_session_title_patches_the_portal_row(portal):
    await client.publish_session_title(agent_id="agent-1", session_id="s-1", title="  Release pipeline permissions ")

    assert portal.calls == [
        {
            "url": "http://portal.internal/api/internal/agents/agent-1/sessions/s-1/title",
            "json": {"title": "Release pipeline permissions"},
            "headers": {"Content-Type": "application/json"},
        }
    ]


async def test_publish_session_title_is_quiet_about_failures(portal, caplog):
    portal.status = 404
    await client.publish_session_title(agent_id="agent-1", session_id="s-1", title="x")  # no exception
    assert len(portal.calls) == 1


@pytest.mark.parametrize("agent_id, session_id, title", [("", "s-1", "x"), ("a", "", "x"), ("a", "s-1", "  ")])
async def test_publish_session_title_skips_incomplete_input(portal, agent_id, session_id, title):
    await client.publish_session_title(agent_id=agent_id, session_id=session_id, title=title)
    assert portal.calls == []


async def test_publish_session_title_needs_a_portal(monkeypatch, portal):
    monkeypatch.setattr(client, "get_portal_internal_base_url", lambda: "")
    await client.publish_session_title(agent_id="a", session_id="s-1", title="x")
    assert portal.calls == []
