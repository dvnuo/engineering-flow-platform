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
    """What `question/respond` writes, built by the code that writes it.

    The envelope is taken from the production helper rather than retyped, so a
    rename in the writer fails these tests instead of silently losing every
    answer in production.
    """
    from src.gateway.runtime_api import _pending_question_tool_call_id

    metadata = dict(store.get_session(session_id).metadata)
    metadata["pending_question_answer"] = {
        "tool_call_id": _pending_question_tool_call_id(pending),
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
@pytest.mark.parametrize(
    "held",
    [
        {"answers": ["EFP"]},                        # no tool_call_id
        {"tool_call_id": "call-A", "answers": 42},    # not iterable at all
        {"tool_call_id": "call-A", "answers": [0]},   # an option index
        {"tool_call_id": "call-A", "answers": {"q": "EFP"}},  # a mapping
        {"tool_call_id": "call-A"},                   # no answers
    ],
    ids=["no-call-id", "scalar", "index", "mapping", "no-answers"],
)
async def test_an_unusable_held_answer_leaves_the_session_runnable(
    tmp_path: Path, workspace: Path, held: dict[str, Any]
):
    """Session metadata is not validated on write, and this is read on replay.

    A value the broker could not read used to raise out of the run. Persisted,
    that raise came back on every later run of the session -- including plain
    chat messages -- with nothing able to clear it. An unusable answer has to
    mean "no answer", not "this session is over".
    """
    store = FileSessionStore(tmp_path / "store")
    await _ask(store, workspace, "s5")
    metadata = dict(store.get_session("s5").metadata)
    metadata["pending_question_answer"] = held
    store.update_session("s5", metadata=metadata, replace_metadata=True)

    result = await _runtime(store, workspace, ScriptedLLMProvider([{"content": "unused"}])).resume("s5")
    assert result.status == "waiting_for_question"

    # and the session still runs at all -- this is the part that used to break
    again = await _runtime(store, workspace, ScriptedLLMProvider([{"content": "unused"}])).run(
        "status?", session_id="s5"
    )
    assert again.status == "waiting_for_question"


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


@pytest.mark.asyncio
async def test_an_answer_survives_a_run_that_ended_before_replaying_the_call(
    tmp_path: Path, workspace: Path
):
    """A cancel is not a consumption.

    A run stopped before the replay reaches the question tool ends with no
    pending question in its result while the call is still unpaired. Clearing
    on that discarded an answer the member had already given, which is the
    failure this whole mechanism exists to prevent.
    """
    from types import SimpleNamespace

    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeSessionManager(store=store)
    pending = await _ask(store, workspace, "s6")
    await manager.record_runtime_result(
        "s6", SimpleNamespace(status="waiting_for_question", pending_question_request=pending,
                              pending_permission_request=None, runtime_events=[], usage={}),
        request_id="r1",
    )
    _record_answer(store, "s6", pending, ["EFP"])

    cancelled = SimpleNamespace(status="cancelled", pending_question_request=None,
                                pending_permission_request=None, runtime_events=[], usage={})
    await manager.record_runtime_result("s6", cancelled, request_id="r2")

    assert "pending_question_answer" in store.get_session("s6").metadata, "the call is still unpaired"

    provider = ScriptedLLMProvider([{"content": "Filed EFP-123."}])
    recovered = await _runtime(store, workspace, provider).resume("s6")
    assert recovered.status == "completed"
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_an_answer_for_a_call_that_now_has_a_result_is_retired(
    tmp_path: Path, workspace: Path
):
    # Consumed, then the run asked something else: the old answer must not sit
    # there to be handed back if that call is ever replayed again.
    store = FileSessionStore(tmp_path / "store")
    manager = RuntimeSessionManager(store=store)
    pending = await _ask(store, workspace, "s7")
    _record_answer(store, "s7", pending, ["EFP"])

    asks_again = ScriptedLLMProvider([{"tool_calls": [_tool_call("call-B", "question", QUESTION_ARGS)]}])
    result = await _runtime(store, workspace, asks_again).resume("s7")
    await manager.record_runtime_result("s7", result, request_id="r2")

    metadata = store.get_session("s7").metadata
    assert metadata["pending_question_request"]["tool_call_id"] == "call-B"
    assert "pending_question_answer" not in metadata


@pytest.mark.parametrize(
    "answers,ok",
    [
        (["EFP"], True), ("EFP", True), ([["EFP", "Portal"]], True),
        (42, False), ([0], False), ({"q": "EFP"}, False),
        ([{"label": "EFP"}], False), ([], False), (None, False),
        (["x" * 70000], False),
    ],
)
def test_the_endpoint_refuses_a_body_that_is_not_an_answer(answers: Any, ok: bool):
    """Rejected at the door rather than persisted.

    An unusable value cost one failed run before; persisted it would be read
    back by every later run instead. A mapping is the dangerous one -- it did
    not raise, it iterated to its keys and told the model the member had
    chosen "label".
    """
    from src.gateway.runtime_api import _question_answers_error

    assert (_question_answers_error(answers) is None) is ok
