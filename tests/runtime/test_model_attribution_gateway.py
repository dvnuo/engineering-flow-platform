"""The gateway tells the Portal which model answered, live and in history."""
from __future__ import annotations

from src.efp_runtime.loop.runner import LoopStatus, RuntimeLoopResult
from src.efp_runtime.session.models import Message
from src.gateway import runtime_chat
from src.gateway.runtime_api import _normalize_chat_history_message


def _result(metadata: dict | None) -> RuntimeLoopResult:
    message = None
    if metadata is not None:
        message = Message.from_text("assistant", "Done.", session_id="s-1", metadata=metadata)
    return RuntimeLoopResult(
        session_id="s-1",
        final_assistant_message=message,
        iterations=1,
        status=LoopStatus.COMPLETED,
    )


def test_result_payload_names_the_model_that_answered_an_auto_run():
    payload = runtime_chat._result_payload(
        _result({"model": "gpt-6.1-sol", "requested_model": "auto"}),
        request_id="req-1",
        model="auto",
    )

    assert payload["model"] == "gpt-6.1-sol"
    assert payload["requested_model"] == "auto"
    assert "model_fallback_reason" not in payload


def test_result_payload_carries_the_fallback_reason():
    payload = runtime_chat._result_payload(
        _result({"model": "gpt-6-sol", "requested_model": "auto", "model_fallback_reason": "auto session unavailable: HTTP 404"}),
        request_id="req-2",
        model="auto",
    )

    assert payload["model"] == "gpt-6-sol"
    assert payload["model_fallback_reason"] == "auto session unavailable: HTTP 404"


def test_result_payload_falls_back_to_the_requested_model_without_a_message():
    payload = runtime_chat._result_payload(_result(None), request_id="req-3", model="gpt-6-luna")

    assert payload["model"] == "gpt-6-luna"
    assert "requested_model" not in payload


def test_history_message_exposes_the_model_fields_top_level():
    normalized = _normalize_chat_history_message(
        {
            "role": "assistant",
            "content": "Done.",
            "metadata": {"model": "gpt-6.1-sol", "requested_model": "auto", "model_fallback_reason": "x"},
        },
        portal_user_id=None,
        portal_user_name=None,
        runtime_agent_id="agent-1",
        runtime_agent_name="Helper",
    )

    assert normalized["model"] == "gpt-6.1-sol"
    assert normalized["requested_model"] == "auto"
    assert normalized["model_fallback_reason"] == "x"
    # An older message without the stamp gains nothing.
    plain = _normalize_chat_history_message(
        {"role": "assistant", "content": "Old.", "metadata": {}},
        portal_user_id=None,
        portal_user_name=None,
        runtime_agent_id=None,
        runtime_agent_name=None,
    )
    assert "model" not in plain
