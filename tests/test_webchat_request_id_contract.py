"""Import-light contract tests for webchat request-id behavior."""

from __future__ import annotations

from tests._import_helpers import load_module_from_repo_path


def _load_contracts_module():
    return load_module_from_repo_path(
        "src.gateway.webchat_request_contracts",
        "src/gateway/webchat_request_contracts.py",
    )


def _load_chat_payloads_module():
    return load_module_from_repo_path(
        "src.gateway.chat_payloads",
        "src/gateway/chat_payloads.py",
    )


def test_extract_trusted_client_request_id_accepts_trusted_client_request_id():
    mod = _load_contracts_module()
    request_id = mod.extract_trusted_client_request_id(
        is_trusted_portal_request=True,
        data={"client_request_id": "portal-chat-req-1"},
    )
    assert request_id == "portal-chat-req-1"


def test_extract_trusted_client_request_id_rejects_untrusted_client_request_id():
    mod = _load_contracts_module()
    request_id = mod.extract_trusted_client_request_id(
        is_trusted_portal_request=False,
        data={"client_request_id": "attacker-id"},
    )
    assert request_id is None


def test_extract_trusted_client_request_id_prefers_client_request_id_then_request_id():
    mod = _load_contracts_module()
    preferred = mod.extract_trusted_client_request_id(
        is_trusted_portal_request=True,
        data={"client_request_id": "client-1", "request_id": "fallback-1"},
    )
    fallback = mod.extract_trusted_client_request_id(
        is_trusted_portal_request=True,
        data={"request_id": "fallback-2"},
    )
    assert preferred == "client-1"
    assert fallback == "fallback-2"


def test_extract_trusted_client_request_id_rejects_invalid_values():
    mod = _load_contracts_module()
    too_long = "a" * 129
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": ""}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": "   "}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": 123}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": too_long}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": "bad/id"}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": "bad id"}) is None
    assert mod.extract_trusted_client_request_id(True, {"client_request_id": "bad?id"}) is None


def test_build_stream_start_event_payload_contract():
    mod = _load_contracts_module()
    payload = mod.build_stream_start_event_payload("s-1", "req-1")
    assert payload == {"session_id": "s-1", "request_id": "req-1"}


def test_build_webchat_response_payload_request_id_priority_prefers_top_level():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {
            "response": "ok",
            "request_id": "top-1",
            "_execution_result": type("ExecutionResult", (), {"request_id": "exec-1"})(),
        },
        "s-priority",
    )
    assert payload["request_id"] == "top-1"


def test_build_webchat_response_payload_request_id_backfills_from_execution_result():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {
            "response": "ok",
            "_execution_result": type("ExecutionResult", (), {"request_id": "exec-1"})(),
        },
        "s-backfill",
    )
    assert payload["request_id"] == "exec-1"


def test_build_webchat_response_payload_includes_context_state():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {"response": "ok", "context_state": {"budget": {"usage_percent": 42.0}}},
        "s1",
    )
    assert payload["context_state"]["budget"]["usage_percent"] == 42.0
