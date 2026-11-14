"""A run resumed by a card answer stays on the model of the turn that asked.

A chat post names its model (``model_override``, the member's choice in the
Portal) or runs on the assistant's configured one. The answer to a question
or permission card is a different request, and the resume read no model from
it at all: it always ran on the configured model. A turn started on another
model changed models halfway through, and on an account that is not served
the configured model the answer failed outright ("The requested model is not
supported"), with the system error lost on the way to the transcript because
the failure handler called its helper without a required argument.

The session now keeps the asking turn's model next to its connectors, the
respond endpoints hand the resume the model the Portal names or else the
kept one, and a failed resume leaves its error in the transcript.
"""
from __future__ import annotations

import asyncio
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.efp_runtime.session.file_store import FileSessionStore
from src.efp_runtime.session.gateway_facade import RuntimeSessionManager
from src.gateway import runtime_api, runtime_chat


CONNECTORS = {"local_bridge": {"enabled": True, "client_id": "tab-asking", "protocol_version": 1, "config": {}}}
PENDING = {"request_id": "q-1", "tool_call_id": "call-1", "questions": [{"question": "Which branch?"}]}
WAITING = dict(status="waiting_for_question", pending_permission_request=None, pending_question_request=PENDING)
PORTAL = {"X-Portal-Author-Source": "portal"}


@pytest.fixture()
def copilot_config(monkeypatch):
    monkeypatch.setattr(
        runtime_chat.config,
        "_config",
        {"llm": {"provider": "github_copilot", "api_key": "copilot-config-token", "model": "gpt-6-sol"}},
        raising=False,
    )


class _Request:
    def __init__(self, session_id: str, body: dict, headers: dict | None = None):
        self.match_info = {"session_id": session_id}
        self._body = body
        self.headers = PORTAL if headers is None else headers

    async def json(self):
        return self._body


# ---------------------------------------------------------------------------
# the session keeps the turn's model while a card is pending


@pytest.mark.asyncio
async def test_facade_keeps_the_turns_model_while_it_waits_on_a_card(tmp_path: Path):
    manager = RuntimeSessionManager(store=FileSessionStore(tmp_path / "store"))

    await manager.record_runtime_result(
        "s-1",
        SimpleNamespace(**WAITING),
        request_id="req-1",
        execution_metadata={"connectors": CONNECTORS, "enable_browser_tool": True},
        requested_model="auto",
    )
    assert manager.store.get_session("s-1").metadata["pending_input_context"] == {
        "connectors": CONNECTORS,
        "enable_browser_tool": True,
        "model": "auto",
    }

    # A turn with no connectors still has a model to come back to.
    await manager.record_runtime_result(
        "s-1", SimpleNamespace(**WAITING), request_id="req-2", requested_model=" gpt-6-luna "
    )
    assert manager.store.get_session("s-1").metadata["pending_input_context"] == {"model": "gpt-6-luna"}

    # Nothing is kept once nothing is pending.
    await manager.record_runtime_result(
        "s-1",
        SimpleNamespace(status="completed", pending_permission_request=None, pending_question_request=None),
        request_id="req-3",
        requested_model="gpt-6-luna",
    )
    assert "pending_input_context" not in manager.store.get_session("s-1").metadata


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", ["run", "resume"])
async def test_a_chat_run_records_the_model_it_was_asked_for(monkeypatch, copilot_config, entry_point):
    recorded: dict = {}

    class _FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        async def run(self, _prompt, *, session_id, **_kwargs):
            return RuntimeLoopResult(
                session_id=session_id,
                final_assistant_message=None,
                iterations=1,
                status=LoopStatus.WAITING_FOR_QUESTION,
                pending_question_request=PENDING,
            )

        async def resume(self, *, session_id, **_kwargs):
            return await self.run("", session_id=session_id)

    class _FakeSessionManager:
        async def record_runtime_result(self, session_id, _result, **kwargs):
            recorded[session_id] = kwargs

    monkeypatch.setattr(runtime_chat, "_build_github_copilot_provider", lambda model: object())
    monkeypatch.setattr(runtime_chat, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(runtime_chat, "get_runtime_session_manager", lambda: _FakeSessionManager())
    monkeypatch.setattr(runtime_chat, "get_runtime_session_store", lambda: object())
    monkeypatch.setattr(runtime_chat, "_seed_runtime_question_response", lambda *_a, **_k: None)

    if entry_point == "run":
        await runtime_chat.run_runtime_chat(message="hello", session_id="s-run", model="auto-intelligence")
    else:
        await runtime_chat.resume_runtime_chat(session_id="s-run", model="auto-intelligence")

    assert recorded["s-run"]["requested_model"] == "auto-intelligence"


# ---------------------------------------------------------------------------
# which model an answer resumes on


def test_the_model_the_portal_names_wins(copilot_config):
    model = runtime_api._resumed_turn_model(
        _Request("s", {}),
        {"model_override": " gpt-6-luna "},
        {"input_context": {"model": "auto"}},
    )
    assert model == "gpt-6-luna"


def test_an_answer_that_names_no_model_resumes_on_the_asking_turns(copilot_config):
    assert runtime_api._resumed_turn_model(_Request("s", {}), {}, {"input_context": {"model": "auto"}}) == "auto"
    assert (
        runtime_api._resumed_turn_model(
            _Request("s", {}), {}, {"input_context": {"connectors": CONNECTORS, "model": "gpt-6.1-sol"}}
        )
        == "gpt-6.1-sol"
    )


def test_a_model_named_by_anyone_but_the_portal_is_not_read(copilot_config):
    direct = _Request("s", {}, headers={})
    assert runtime_api._resumed_turn_model(direct, {"model_override": "gpt-6-luna"}, {"input_context": None}) is None
    # The kept model is the runtime's own record, whoever sends the answer.
    assert (
        runtime_api._resumed_turn_model(direct, {"model_override": "gpt-6-luna"}, {"input_context": {"model": "auto"}})
        == "auto"
    )


def test_nothing_kept_and_nothing_named_is_the_configured_model(copilot_config):
    request = _Request("s", {})
    assert runtime_api._resumed_turn_model(request, {}, {}) is None
    assert runtime_api._resumed_turn_model(request, {}, {"input_context": None}) is None
    assert runtime_api._resumed_turn_model(request, {}, {"input_context": {"connectors": CONNECTORS}}) is None
    assert runtime_api._resumed_turn_model(request, {}, {"input_context": {"model": "  "}}) is None
    assert runtime_api._resumed_turn_model(request, {}, "not-a-mapping") is None


def test_a_kept_model_this_runtime_no_longer_lists_is_dropped(monkeypatch, copilot_config):
    # A card can wait across an upgrade that retires the model it was asked on.
    monkeypatch.setattr(runtime_chat, "_admit_model_from_copilot_catalog", lambda _text: None)
    request = _Request("s", {})
    assert runtime_api._resumed_turn_model(request, {}, {"input_context": {"model": "gpt-5.6-terra"}}) is None
    assert runtime_chat.model_still_runnable("gpt-5.6-terra") is False
    assert runtime_chat.model_still_runnable("gpt-6-luna") is True
    assert runtime_chat.model_still_runnable("auto-efficiency") is True
    assert runtime_chat.model_still_runnable(None) is False


def test_an_ai_platform_assistant_keeps_whatever_was_kept(monkeypatch):
    # That provider coerces an id it does not serve; it never refuses one.
    monkeypatch.setattr(
        runtime_chat.config, "_config", {"llm": {"provider": "ai_platform", "model": "gpt-5.4"}}, raising=False
    )
    assert runtime_chat.model_still_runnable("gpt-5.6-sol") is True
    assert runtime_api._resumed_turn_model(_Request("s", {}), {}, {"input_context": {"model": "gpt-5.6-sol"}}) == "gpt-5.6-sol"


# ---------------------------------------------------------------------------
# the respond endpoints hand the model to the resume


def _resumed_with(monkeypatch, *, pending_state: dict, body: dict, endpoint) -> dict:
    captured: dict = {}

    async def fake_pending(_session_id):
        return pending_state

    async def noop(*_a, **_k):
        return None

    async def fake_resume(_request, **kwargs):
        captured.update(kwargs)
        return {"status": "accepted", "request_id": "chat-resume-1"}

    monkeypatch.setattr(runtime_api, "_session_pending_input", fake_pending)
    monkeypatch.setattr(runtime_api, "_record_pending_question_answer", noop)
    monkeypatch.setattr(runtime_api, "_retire_pending_request", noop)
    monkeypatch.setattr(runtime_api, "_resume_chat_after_user_input", fake_resume)
    monkeypatch.setattr(runtime_api, "_claim_pending_request", lambda *_a: True)
    monkeypatch.setattr(runtime_api, "_release_pending_claim", lambda *_a: None)

    response = asyncio.run(endpoint(_Request("s-4", body)))
    assert response.status == 202, response.text
    return captured


def test_a_question_answer_resumes_on_the_asking_turns_model(monkeypatch, copilot_config):
    resumed = _resumed_with(
        monkeypatch,
        pending_state={"question_request": PENDING, "permission_request": None, "input_context": {"model": "auto"}},
        body={"request_id": "q-1", "answers": [["main"]]},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert resumed["model"] == "auto"


def test_a_question_answer_resumes_on_the_model_the_portal_filled_in(monkeypatch, copilot_config):
    resumed = _resumed_with(
        monkeypatch,
        pending_state={"question_request": PENDING, "permission_request": None, "input_context": {"model": "auto"}},
        body={"request_id": "q-1", "answers": "main", "model_override": "gpt-6.1-sol"},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert resumed["model"] == "gpt-6.1-sol"


def test_a_permission_answer_resumes_on_the_asking_turns_model(monkeypatch, copilot_config):
    pending = {"request_id": "p-1", "tool_id": "shell", "args": {"command": "ls"}}
    resumed = _resumed_with(
        monkeypatch,
        pending_state={"question_request": None, "permission_request": pending, "input_context": {"model": "gpt-6-luna"}},
        body={"request_id": "p-1", "decision": "approve"},
        endpoint=runtime_api.api_session_permission_respond,
    )
    assert resumed["model"] == "gpt-6-luna"


def test_an_answer_to_a_card_parked_before_the_model_was_kept_names_none(monkeypatch, copilot_config):
    resumed = _resumed_with(
        monkeypatch,
        pending_state={"question_request": PENDING, "permission_request": None, "input_context": None},
        body={"request_id": "q-1", "answers": "main"},
        endpoint=runtime_api.api_session_question_respond,
    )
    assert resumed["model"] is None


# ---------------------------------------------------------------------------
# the resume itself


def _patch_resume_surroundings(monkeypatch, fake_resume):
    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(runtime_api, "emit_agent_event", noop)
    monkeypatch.setattr(runtime_api, "resume_runtime_chat", fake_resume)
    monkeypatch.setattr(runtime_api, "global_config", types.SimpleNamespace(llm={"model": "gpt-6-sol"}))
    monkeypatch.setattr(runtime_api, "_resolve_runtime_agent_identity", lambda _r: ("agent-1", "Agent"))


def _run_resume(**kwargs) -> dict:
    async def run():
        started = await runtime_api._resume_chat_after_user_input(object(), **kwargs)
        await asyncio.wait_for(asyncio.gather(*list(runtime_api._RESUME_TASKS)), timeout=5)
        return started

    return asyncio.run(run())


def test_the_resume_runs_on_the_model_it_was_given(monkeypatch):
    seen: dict = {}

    async def fake_resume(**kwargs):
        seen.update(kwargs)
        return {"status": "completed", "response": "done"}

    async def noop(*_a, **_k):
        return None

    _patch_resume_surroundings(monkeypatch, fake_resume)
    monkeypatch.setattr(runtime_api, "session_manager", types.SimpleNamespace(mark_runtime_running=noop))
    projected: list = []
    real_projector = runtime_api.RuntimeEventProjector

    def spy_projector(**kwargs):
        projected.append(kwargs.get("model"))
        return real_projector(**kwargs)

    monkeypatch.setattr(runtime_api, "RuntimeEventProjector", spy_projector)

    _run_resume(session_id="s-model", execution_metadata={}, model="auto")
    assert seen["model"] == "auto"
    assert projected == ["auto"]

    # No model named: the run falls to the configured one, as it always did.
    _run_resume(session_id="s-model", execution_metadata={})
    assert seen["model"] is None
    assert projected == ["auto", "gpt-6-sol"]


def test_a_failed_resume_leaves_its_error_in_the_transcript(monkeypatch):
    # The real helper, not a stand-in: it takes ``metadata`` as a required
    # keyword, and the call that left it out raised inside a handler that
    # swallowed the TypeError, so the member was told to "review the last
    # system error" and there was none to review.
    added: list[tuple[str, str, str, dict]] = []
    published: list[dict] = []

    async def fake_resume(**_kwargs):
        raise RuntimeError("The requested model is not supported.")

    async def noop(*_a, **_k):
        return None

    async def add_message(session_id, role, content, **kwargs):
        added.append((session_id, role, content, kwargs.get("extra") or {}))

    async def fake_publish(**kwargs):
        published.append(kwargs)

    _patch_resume_surroundings(monkeypatch, fake_resume)
    monkeypatch.setattr(
        runtime_api,
        "session_manager",
        types.SimpleNamespace(mark_runtime_running=noop, add_message=add_message, _initialized=True),
    )
    monkeypatch.setattr(runtime_api, "publish_session_metadata", fake_publish)

    started = _run_resume(session_id="s-failed", execution_metadata={"portal_user": {"id": "7"}}, model="auto")

    assert runtime_api.chat_run_registry.get(started["request_id"]).state == "failed"
    assert len(added) == 1
    session_id, role, content, extra = added[0]
    assert (session_id, role) == ("s-failed", "assistant")
    assert content == "System error: The requested model is not supported."
    assert extra["metadata"]["kind"] == "system_error"
    assert extra["metadata"]["request_id"] == started["request_id"]
    assert extra["exclude_from_model_context"] is True
    assert published and published[0]["latest_event_type"] == "chat.failed"
    assert published[0]["metadata"] == {"portal_user": {"id": "7"}}
