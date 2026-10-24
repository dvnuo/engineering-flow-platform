"""A run resumed by a card answer keeps the browser tool of the turn that asked.

The browser tool is registered only when the run's trusted metadata names
``connectors.local_bridge``. Portal injects that on chat posts; the answer to
a question or permission card is a different request, and an older Portal
sends it without connectors. The resumed run then had no browser tool, so
right after the member agreed to an upload the assistant answered "this
session has no browser tool". The session now keeps the asking turn's
connectors as ``pending_input_context`` and the respond endpoints fall back
to them.
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from efp_runtime.session.file_store import FileSessionStore
from efp_runtime.session.gateway_facade import RuntimeSessionManager
from src.gateway import runtime_api
from src.gateway.runtime_chat import _execution_metadata_enables_browser_tool


CONNECTORS = {"local_bridge": {"enabled": True, "client_id": "tab-asking", "protocol_version": 1, "config": {}}}
PENDING = {"request_id": "q-1", "tool_call_id": "call-1", "questions": [{"question": "Upload?"}]}


# ---------------------------------------------------------------------------
# the metadata merge


def test_answer_without_connectors_falls_back_to_the_asking_turn():
    merged = runtime_api._metadata_with_pending_input_context(
        {"portal_user": {"id": "7"}},
        {"question_request": PENDING, "input_context": {"connectors": CONNECTORS, "enable_browser_tool": True}},
    )
    assert merged["connectors"] == CONNECTORS
    assert merged["enable_browser_tool"] is True
    assert merged["portal_user"] == {"id": "7"}
    assert _execution_metadata_enables_browser_tool(merged) is True


def test_answer_that_names_connectors_wins():
    answering = {"local_bridge": {"enabled": True, "client_id": "tab-answering", "protocol_version": 1}}
    merged = runtime_api._metadata_with_pending_input_context(
        {"connectors": answering, "enable_browser_tool": True},
        {"input_context": {"connectors": CONNECTORS, "enable_browser_tool": True}},
    )
    assert merged["connectors"] == answering


def test_nothing_kept_means_nothing_added():
    assert runtime_api._metadata_with_pending_input_context({}, {"input_context": None}) == {}
    assert runtime_api._metadata_with_pending_input_context({}, {}) == {}
    assert runtime_api._metadata_with_pending_input_context({}, {"input_context": {"connectors": {}}}) == {}
    assert runtime_api._metadata_with_pending_input_context(None, "not-a-mapping") == {}


def test_fallback_copies_rather_than_shares_the_stored_block():
    context = {"connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1"}}}
    merged = runtime_api._metadata_with_pending_input_context({}, {"input_context": context})
    merged["connectors"]["local_bridge"]["client_id"] = "changed"
    assert context["connectors"]["local_bridge"]["client_id"] == "tab-1"


# ---------------------------------------------------------------------------
# the session keeps the connectors only while a card is pending


@pytest.mark.asyncio
async def test_facade_keeps_connectors_while_the_turn_waits_on_a_card(tmp_path: Path):
    manager = RuntimeSessionManager(store=FileSessionStore(tmp_path / "store"))
    execution_metadata = {"connectors": CONNECTORS, "enable_browser_tool": True, "portal_user": {"id": "7"}}

    await manager.record_runtime_result(
        "s-1",
        SimpleNamespace(status="waiting_for_question", pending_permission_request=None, pending_question_request=PENDING),
        request_id="req-1",
        execution_metadata=execution_metadata,
    )
    metadata = manager.store.get_session("s-1").metadata
    assert metadata["pending_question_request"] == PENDING
    # Only the connectors travel; identity and the rest stay out of it.
    assert metadata["pending_input_context"] == {"connectors": CONNECTORS, "enable_browser_tool": True}

    await manager.record_runtime_result(
        "s-1",
        SimpleNamespace(status="completed", pending_permission_request=None, pending_question_request=None),
        request_id="req-2",
        execution_metadata=execution_metadata,
    )
    metadata = manager.store.get_session("s-1").metadata
    assert "pending_question_request" not in metadata
    assert "pending_input_context" not in metadata


@pytest.mark.asyncio
async def test_facade_keeps_nothing_for_a_turn_without_connectors(tmp_path: Path):
    manager = RuntimeSessionManager(store=FileSessionStore(tmp_path / "store"))
    await manager.record_runtime_result(
        "s-2",
        SimpleNamespace(status="waiting_for_permission", pending_permission_request={"request_id": "p-1"}, pending_question_request=None),
        request_id="req-1",
        execution_metadata={"portal_user": {"id": "7"}},
    )
    metadata = manager.store.get_session("s-2").metadata
    assert metadata["pending_permission_request"] == {"request_id": "p-1"}
    assert "pending_input_context" not in metadata

    # A caller that does not pass metadata at all (tasks, older call sites).
    await manager.record_runtime_result(
        "s-2",
        SimpleNamespace(status="waiting_for_permission", pending_permission_request={"request_id": "p-1"}, pending_question_request=None),
        request_id="req-1",
    )
    assert "pending_input_context" not in manager.store.get_session("s-2").metadata


@pytest.mark.asyncio
async def test_pending_input_read_exposes_the_context(monkeypatch):
    async def fake_get_existing_session(session_id):
        return {
            "metadata": {
                "pending_question_request": PENDING,
                "pending_input_context": {"connectors": CONNECTORS, "enable_browser_tool": True},
            }
        }

    monkeypatch.setattr(runtime_api, "session_manager", types.SimpleNamespace(get_existing_session=fake_get_existing_session))
    state = await runtime_api._session_pending_input("s-3")
    assert state["question_request"] == PENDING
    assert state["input_context"] == {"connectors": CONNECTORS, "enable_browser_tool": True}


# ---------------------------------------------------------------------------
# the respond endpoints resume with the fallback


class _Request:
    def __init__(self, session_id: str, body: dict, headers: dict | None = None):
        self.match_info = {"session_id": session_id}
        self._body = body
        self.headers = headers or {"X-Portal-Author-Source": "portal"}

    async def json(self):
        return self._body


def _resumed_metadata(monkeypatch, *, pending_state: dict, body: dict, endpoint) -> dict:
    captured: dict = {}

    async def fake_pending(session_id):
        return pending_state

    async def fake_record(*_a, **_k):
        return None

    async def fake_resume(request, *, session_id, execution_metadata, release_claim=None):
        captured["execution_metadata"] = execution_metadata
        return {"status": "accepted", "request_id": "chat-resume-1"}

    monkeypatch.setattr(runtime_api, "_session_pending_input", fake_pending)
    monkeypatch.setattr(runtime_api, "_record_pending_question_answer", fake_record)
    monkeypatch.setattr(runtime_api, "_resume_chat_after_user_input", fake_resume)
    monkeypatch.setattr(runtime_api, "_claim_pending_request", lambda *_a: True)
    monkeypatch.setattr(runtime_api, "_release_pending_claim", lambda *_a: None)

    response = asyncio.run(endpoint(_Request("s-4", body)))
    assert response.status == 202, response.text
    return captured["execution_metadata"]


def test_question_answer_without_connectors_resumes_with_the_asking_turns(monkeypatch):
    metadata = _resumed_metadata(
        monkeypatch,
        pending_state={
            "question_request": PENDING,
            "permission_request": None,
            "input_context": {"connectors": CONNECTORS, "enable_browser_tool": True},
        },
        body={"request_id": "q-1", "answers": [["yes"]]},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert metadata["connectors"] == CONNECTORS
    assert metadata["enable_browser_tool"] is True
    assert metadata["runtime_question_response"]["answers"] == [["yes"]]
    assert _execution_metadata_enables_browser_tool(metadata) is True


def test_question_answer_with_connectors_keeps_its_own(monkeypatch):
    answering = {"local_bridge": {"enabled": True, "client_id": "tab-answering", "protocol_version": 1}}
    metadata = _resumed_metadata(
        monkeypatch,
        pending_state={
            "question_request": PENDING,
            "permission_request": None,
            "input_context": {"connectors": CONNECTORS, "enable_browser_tool": True},
        },
        body={"request_id": "q-1", "answers": "yes", "metadata": {"connectors": answering, "enable_browser_tool": True}},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert metadata["connectors"] == answering


def test_permission_answer_without_connectors_resumes_with_the_asking_turns(monkeypatch):
    pending = {"request_id": "p-1", "tool_id": "shell", "args": {"command": "ls"}}
    metadata = _resumed_metadata(
        monkeypatch,
        pending_state={
            "question_request": None,
            "permission_request": pending,
            "input_context": {"connectors": CONNECTORS, "enable_browser_tool": True},
        },
        body={"request_id": "p-1", "decision": "approve"},
        endpoint=runtime_api.api_session_permission_respond,
    )
    assert metadata["connectors"] == CONNECTORS
    assert metadata["enable_browser_tool"] is True
    assert metadata["runtime_profile"]["config"]["tool_permissions"]["shell"]["action"] == "allow"


def test_question_answer_for_a_turn_without_connectors_adds_none(monkeypatch):
    metadata = _resumed_metadata(
        monkeypatch,
        pending_state={"question_request": PENDING, "permission_request": None, "input_context": None},
        body={"request_id": "q-1", "answers": "yes"},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert "connectors" not in metadata
    assert _execution_metadata_enables_browser_tool(metadata) is False
    json.dumps(metadata)  # stays serialisable for the run registry
