"""Import-light contract tests for WebChat payload normalization helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_chat_payloads_module():
    repo_root = Path(__file__).parent.parent
    module_path = repo_root / "src" / "gateway" / "chat_payloads.py"
    spec = importlib.util.spec_from_file_location("webchat_payloads_contract", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_build_webchat_response_payload_falls_back_to_content():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload({"content": "hello"}, "s-1")

    assert payload["response"] == "hello"
    assert payload["session_id"] == "s-1"
    assert payload["display_blocks"] == [{"type": "markdown", "content": "hello"}]


def test_build_webchat_response_payload_preserves_block_only_response():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {
            "response": "",
            "display_blocks": [
                {"type": "tool_result", "title": "Bash", "status": "success", "output": "done"},
            ],
        },
        "s-2",
    )

    assert payload["response"] == ""
    assert payload["display_blocks"][0]["type"] == "tool_result"
    assert payload["display_blocks"][0]["content"] == "done"


def test_build_webchat_response_payload_falls_back_for_invalid_blocks():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {
            "response": "hello",
            "display_blocks": [None, "bad", {"type": "   "}],
        },
        "s-3",
    )

    assert payload["display_blocks"] == [{"type": "markdown", "content": "hello"}]


def test_normalize_assistant_history_message_backfills_display_blocks():
    mod = _load_chat_payloads_module()
    message = mod.normalize_assistant_history_message({"role": "assistant", "content": "hello"})

    assert message["role"] == "assistant"
    assert message["content"] == "hello"
    assert message["display_blocks"] == [{"type": "markdown", "content": "hello"}]


def test_normalize_assistant_history_message_leaves_non_assistant_shape():
    mod = _load_chat_payloads_module()
    original = {"role": "user", "content": "hello"}
    message = mod.normalize_assistant_history_message(original)

    assert message["role"] == "user"
    assert message["content"] == "hello"
    assert "display_blocks" not in message
