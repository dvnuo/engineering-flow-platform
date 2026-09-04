"""An answer has to outlive the one run that received it.

The question a run stops on is durable twice over: an unpaired tool call in
history, and `pending_question_request` in session metadata. Every later run
replays it before anything else.

The answer used to live only in the `QuestionBroker` of the single
`AgentRuntime` built for the request that carried it, and `consume_answer`
pops it. So any run that was not that one request found no answer, raised the
identical question again, and returned without ever calling the model -- while
the member's messages reached the transcript and nothing else. Once the answer
was lost there was no way back: the pending call is on disk, the answer was in
a process that had moved on.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.loop import ScriptedLLMProvider
from efp_runtime.runtime import AgentRuntime, RuntimeConfig
from efp_runtime.session.file_store import FileSessionStore
from efp_runtime.session.gateway_facade import RuntimeSessionManager


QUESTION_ARGS = {
    "questions": [
        {"question": "Which project?", "header": "Project", "options": [{"label": "EFP"}]}
    ]
}


def _tool_call(call_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps(arguments)},
    }


def _runtime(store: FileSessionStore, workspace: Path, provider: ScriptedLLMProvider) -> AgentRuntime:
    return AgentRuntime(
        provider=provider,
        store=store,
        config=RuntimeConfig(
            workspace_root=workspace,
            max_iterations=4,
            enable_question_tool=True,
        ),
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


async def _ask(store: FileSessionStore, workspace: Path, session_id: str) -> dict[str, Any]:
    provider = ScriptedLLMProvider([{"tool_calls": [_tool_call("call-A", "question", QUESTION_ARGS)]}])
    result = await _runtime(store, workspace, provider).run("Create a ticket.", session_id=session_id)
    assert result.status == "waiting_for_question"
    return result.pending_question_request


def _record_answer(store: FileSessionStore, session_id: str, pending: dict[str, Any], answers: Any) -> None:
    """What `question/respond` writes onto the session before resuming."""
    metadata = dict(store.get_session(session_id).metadata)
    metadata["pending_question_answer"] = {
        "request_id": pending["request_id"],
        "session_id": pending["session_id"],
        "tool_call_id": pending["tool_call_id"],
        "answers": answers,
    }
    store.update_session(session_id, metadata=metadata, replace_metadata=True)


@pytest.mark.asyncio
async def test_an_answer_is_found_by_a_run_that_did_not_receive_it(tmp_path: Path, workspace: Path):
    # The reproduced failure: the process that took the answer is not the one
    # that replays the call -- a restart, a failed resume, or simply a later run.
    store = FileSessionStore(tmp_path / "store")
    pending = await _ask(store, workspace, "s1")
    _record_answer(store, "s1", pending, ["EFP"])

    provider = ScriptedLLMProvider([{"content": "Filed EFP-123."}])
    result = await _runtime(store, workspace, provider).resume("s1")

    assert result.status == "completed"
    assert result.pending_question_request is None
    assert len(provider.requests) == 1, "the model has to actually be reached"


@pytest.mark.asyncio
async def test_the_model_is_given_the_answer_it_was_waiting_on(tmp_path: Path, workspace: Path):
    store = FileSessionStore(tmp_path / "store")
    pending = await _ask(store, workspace, "s2")
    _record_answer(store, "s2", pending, ["EFP"])

    provider = ScriptedLLMProvider([{"content": "Filed EFP-123."}])
    await _runtime(store, workspace, provider).resume("s2")

    tool_text = [
        part.tool_result.content
        for message in provider.requests[-1].messages
        for part in getattr(message, "parts", [])
        if getattr(part, "tool_result", None) is not None
    ]
    assert any("EFP" in text for text in tool_text)


@pytest.mark.asyncio
async def test_the_answer_is_cleared_with_the_question_it_answered(tmp_path: Path, workspace: Path):
    # Left behind, it would be handed to whatever asks next.
    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeSessionManager(store=store)
    pending = await _ask(store, workspace, "s3")
    await manager.merge_metadata("s3", {
        "pending_question_answer": {
            "request_id": pending["request_id"],
            "session_id": pending["session_id"],
            "tool_call_id": pending["tool_call_id"],
            "answers": ["EFP"],
        }
    })

    provider = ScriptedLLMProvider([{"content": "Filed EFP-123."}])
    result = await _runtime(store, workspace, provider).resume("s3")
    await manager.record_runtime_result("s3", result, request_id="req-2")

    metadata = store.get_session("s3").metadata
    assert "pending_question_request" not in metadata
    assert "pending_question_answer" not in metadata


@pytest.mark.asyncio
async def test_a_question_nobody_has_answered_is_still_raised_again(tmp_path: Path, workspace: Path):
    # The replay is what keeps an unanswered question alive across runs; only
    # an answer should end it.
    store = FileSessionStore(tmp_path / "store")
    first = await _ask(store, workspace, "s4")

    provider = ScriptedLLMProvider([{"content": "unused"}])
    result = await _runtime(store, workspace, provider).run("status?", session_id="s4")

    assert result.status == "waiting_for_question"
    assert result.pending_question_request["request_id"] == first["request_id"]


@pytest.mark.asyncio
async def test_a_malformed_held_answer_is_ignored_rather_than_raising(tmp_path: Path, workspace: Path):
    # It is read back from session metadata, which nothing validates on write.
    store = FileSessionStore(tmp_path / "store")
    await _ask(store, workspace, "s5")
    metadata = dict(store.get_session("s5").metadata)
    metadata["pending_question_answer"] = {"answers": ["EFP"]}  # no tool_call_id
    store.update_session("s5", metadata=metadata, replace_metadata=True)

    provider = ScriptedLLMProvider([{"content": "unused"}])
    result = await _runtime(store, workspace, provider).resume("s5")

    assert result.status == "waiting_for_question"


def test_a_fork_does_not_inherit_an_answer_it_has_no_question_for():
    # Forking drops the pending call and the pending question; an answer left
    # behind would be seeded for a call the fork does not have.
    from efp_runtime.session.fork import fork_session_metadata

    forked = fork_session_metadata(
        {
            "pending_question_request": {"request_id": "q-1"},
            "pending_question_answer": {"tool_call_id": "call-A", "answers": ["EFP"]},
            "title": "kept",
        },
        parent_session_id="s-parent",
    )

    assert "pending_question_answer" not in forked
    assert "pending_question_request" not in forked
    assert forked["title"] == "kept"
