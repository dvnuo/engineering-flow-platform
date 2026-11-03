"""The final chat payload names the stored user turn and the assistant message it answered with.

The Portal draws its per-message actions (Edit, Retry, Delete from here) off
``data-message-id``; the optimistic bubble it paints on send has none until
the runtime reports ``user_message_id``. Before this the native runtime never
did, so the buttons appeared only after a reload.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.efp_runtime.loop.runner import RuntimeLoopResult
from src.gateway.chat_payloads import build_runtime_response_payload
from src.gateway.runtime_chat import _message_ids_from_result, _result_payload


def test_the_response_payload_passes_both_message_ids_through():
    payload = build_runtime_response_payload(
        {"response": "ok", "user_message_id": "msg-user", "assistant_message_id": "msg-assistant"},
        "session-1",
    )
    assert payload["user_message_id"] == "msg-user"
    assert payload["assistant_message_id"] == "msg-assistant"

    bare = build_runtime_response_payload({"response": "ok"}, "session-1")
    assert "user_message_id" not in bare
    assert "assistant_message_id" not in bare


def test_the_result_names_the_stored_turn_ids():
    result = RuntimeLoopResult(
        session_id="session-1",
        final_assistant_message=SimpleNamespace(message_id="msg-assistant", metadata={}),
        iterations=1,
        status="completed",
        user_message_id="msg-user",
    )
    assert _message_ids_from_result(result) == {
        "user_message_id": "msg-user",
        "assistant_message_id": "msg-assistant",
    }

    without = RuntimeLoopResult(session_id="s", final_assistant_message=None, iterations=0, status="completed")
    assert without.user_message_id is None
    assert _message_ids_from_result(without) == {}


def test_result_payload_carries_the_user_message_id_without_an_assistant_message():
    result = RuntimeLoopResult(
        session_id="session-1",
        final_assistant_message=None,
        iterations=1,
        status="completed",
        user_message_id="msg-user",
    )
    payload = _result_payload(result, request_id="req-1", model="gpt-6-sol")
    assert payload["user_message_id"] == "msg-user"
    assert "assistant_message_id" not in payload
    assert payload["request_id"] == "req-1"
