"""An accepted card answer takes the card off the session at once.

The request a run parked on used to stay in session metadata until the
resumed run wrote its result, and every reader took it at face value for as
long as that run took: ``pending-input`` rebuilt the question card after a
page refresh, Portal read the run as parked rather than running and never
followed it, and the member who answered the rebuilt card was refused with
``question_already_answered``. Permission cards had the same gap, and their
claim was never released, so a tool asking the same permission again later
was refused for the life of the process.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from efp_runtime.session.file_store import FileSessionStore
from efp_runtime.session.gateway_facade import RuntimeSessionManager
from src.gateway import runtime_api


QUESTION = {
    "request_id": "q-1",
    "tool_call_id": "call-1",
    "session_id": "s-q",
    "questions": [{"question": "Which project?", "options": [{"label": "EFP"}]}],
}
PERMISSION = {"request_id": "p-1", "tool_id": "shell", "args": {"command": "ls"}}
CONTEXT = {
    "connectors": {"local_bridge": {"enabled": True, "client_id": "tab-1", "protocol_version": 1}},
    "enable_browser_tool": True,
}
COMPLETED = {"status": "completed", "pending_permission_request": None, "pending_question_request": None}


class _Request:
    def __init__(self, session_id: str, body: dict):
        self.match_info = {"session_id": session_id}
        self._body = body
        self.headers = {"X-Portal-Author-Source": "portal"}
        self.app = {}

    async def json(self):
        return self._body


class _Harness:
    """A real session store under the respond endpoints, and a resumed run that runs until released."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.manager = RuntimeSessionManager(store=FileSessionStore(tmp_path / "store"))
        self.gate = asyncio.Event()
        self.ends_with = dict(COMPLETED)
        monkeypatch.setattr(runtime_api, "session_manager", self.manager)
        monkeypatch.setattr(runtime_api, "resume_runtime_chat", self._resume)

    async def _resume(self, *, session_id, request_id, execution_metadata, **_kwargs):
        await self.gate.wait()
        await self.manager.record_runtime_result(
            session_id,
            SimpleNamespace(**self.ends_with),
            request_id=request_id,
            execution_metadata=execution_metadata,
        )
        return {"status": self.ends_with["status"], "request_id": request_id}

    async def park(self, session_id: str, *, question=None, permission=None) -> None:
        """What the asking turn leaves behind when it stops on a card."""
        status = "waiting_for_question" if question is not None else "waiting_for_permission"
        await self.manager.record_runtime_result(
            session_id,
            SimpleNamespace(status=status, pending_permission_request=permission, pending_question_request=question),
            request_id="chat-asking",
            execution_metadata=CONTEXT,
        )

    def metadata(self, session_id: str) -> dict:
        return dict(self.manager.store.get_session(session_id).metadata)

    async def finish_resume(self) -> None:
        self.gate.set()
        await asyncio.gather(*list(runtime_api._RESUME_TASKS))
        self.gate = asyncio.Event()


@pytest.fixture()
def harness(tmp_path: Path, monkeypatch) -> _Harness:
    return _Harness(tmp_path, monkeypatch)


async def _answer(session_id: str, answers="EFP"):
    return await runtime_api.api_session_question_respond(
        _Request(session_id, {"request_id": "q-1", "answers": answers})
    )


async def _approve(session_id: str):
    return await runtime_api.api_session_permission_respond(
        _Request(session_id, {"request_id": "p-1", "decision": "approve"})
    )


# ---------------------------------------------------------------------------
# questions


@pytest.mark.asyncio
async def test_an_answered_question_is_no_longer_pending_while_the_resume_runs(harness: _Harness):
    await harness.park("s-q", question=QUESTION)

    response = await _answer("s-q")
    assert response.status == 202, response.text
    resume_id = json.loads(response.text)["request_id"]

    # What a page refresh asks while the resumed run is still going.
    pending = await runtime_api._session_pending_input("s-q")
    assert pending["question_request"] is None
    assert pending["permission_request"] is None
    metadata = harness.metadata("s-q")
    assert "pending_question_request" not in metadata
    assert "pending_input_context" not in metadata
    # The answer is still held for the call, so a run that dies before
    # replaying it finds it.
    assert metadata["pending_question_answer"] == {"tool_call_id": "call-1", "answers": "EFP"}
    # ...and the session reads as a running run Portal can follow, not a parked one.
    assert metadata["last_runtime_status"] == "running"
    assert metadata["last_execution_id"] == resume_id
    status = await runtime_api._chat_run_status_from_session("s-q", resume_id)
    assert status["state"] == "running"

    await harness.finish_resume()
    after = harness.metadata("s-q")
    assert "pending_question_request" not in after
    # The call has its result; there is nothing left to hold the answer for.
    assert "pending_question_answer" not in after


@pytest.mark.asyncio
async def test_a_second_answer_while_the_resume_runs_is_refused(harness: _Harness):
    await harness.park("s-q2", question=QUESTION)
    assert (await _answer("s-q2")).status == 202

    again = await _answer("s-q2")
    assert again.status == 409
    await harness.finish_resume()


@pytest.mark.asyncio
async def test_a_question_the_resumed_run_asks_again_can_be_answered_again(harness: _Harness):
    # A held answer the tool cannot use ends with the same question re-raised
    # under the same id. Retiring the card must not get in the way of that,
    # and the claim the first answer took must not outlive its run.
    await harness.park("s-q3", question=QUESTION)
    assert (await _answer("s-q3")).status == 202

    harness.ends_with = {**COMPLETED, "status": "waiting_for_question", "pending_question_request": QUESTION}
    await harness.finish_resume()
    assert (await runtime_api._session_pending_input("s-q3"))["question_request"] == QUESTION

    harness.ends_with = dict(COMPLETED)
    again = await _answer("s-q3", answers="EFP, really")
    assert again.status == 202, again.text
    assert harness.metadata("s-q3")["pending_question_answer"]["answers"] == "EFP, really"
    await harness.finish_resume()


@pytest.mark.asyncio
async def test_a_question_without_a_tool_call_id_is_still_retired(harness: _Harness):
    bare = {"request_id": "q-1", "questions": [{"question": "Which project?"}]}
    await harness.park("s-q4", question=bare)

    assert (await _answer("s-q4")).status == 202
    metadata = harness.metadata("s-q4")
    assert "pending_question_request" not in metadata
    # Nothing to hold the answer for; the run about to start carries it.
    assert "pending_question_answer" not in metadata
    await harness.finish_resume()


@pytest.mark.asyncio
async def test_a_store_failure_leaves_the_answer_standing(monkeypatch):
    class _Broken:
        async def replace_metadata_keys(self, *_args, **_kwargs):
            raise RuntimeError("disk full")

    monkeypatch.setattr(runtime_api, "session_manager", _Broken())
    # Logged, not raised: the resumed run still carries the answer.
    await runtime_api._retire_pending_request("s-x", kind="question", held_answer={"tool_call_id": "c", "answers": "a"})


# ---------------------------------------------------------------------------
# permissions


@pytest.mark.asyncio
async def test_an_approved_permission_is_no_longer_pending_while_the_resume_runs(harness: _Harness):
    await harness.park("s-p", permission=PERMISSION)

    response = await _approve("s-p")
    assert response.status == 202, response.text

    pending = await runtime_api._session_pending_input("s-p")
    assert pending["permission_request"] is None
    metadata = harness.metadata("s-p")
    assert "pending_permission_request" not in metadata
    assert "pending_input_context" not in metadata
    assert metadata["last_runtime_status"] == "running"

    assert (await _approve("s-p")).status == 409
    await harness.finish_resume()
    assert "pending_permission_request" not in harness.metadata("s-p")


@pytest.mark.asyncio
async def test_the_permission_claim_is_released_when_the_resume_ends(harness: _Harness):
    await harness.park("s-p2", permission=PERMISSION)
    assert (await _approve("s-p2")).status == 202
    await harness.finish_resume()

    # The tool asks the very same permission again; its id is a hash of the ask.
    await harness.park("s-p2", permission=PERMISSION)
    again = await _approve("s-p2")
    assert again.status == 202, again.text
    await harness.finish_resume()
