"""The gateway payload normalizer keeps ``file`` display blocks intact."""

from __future__ import annotations

from src.gateway.chat_payloads import build_runtime_response_payload, normalize_assistant_history_message, normalize_display_blocks


FILE_BLOCK = {
    "type": "file",
    "path": "output/deck.pptx",
    "name": "deck.pptx",
    "size": 60794,
    "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "action": "created",
    "modified_at": "2026-09-14T08:00:00+00:00",
}


def test_response_payload_keeps_which_model_answered():
    # The stream's final event is built from this payload; the Portal's badge
    # reads the attribution off it, so the whitelist must let it through.
    payload = build_runtime_response_payload(
        {
            "response": "Routed.",
            "usage": {},
            "model": "gpt-6-luna",
            "requested_model": "auto",
            "auto_tier": "balance",
            "model_fallback_reason": "auto route unavailable: HTTP 404",
        },
        "s-model",
    )
    assert payload["model"] == "gpt-6-luna"
    assert payload["requested_model"] == "auto"
    assert payload["auto_tier"] == "balance"
    assert payload["model_fallback_reason"] == "auto route unavailable: HTTP 404"

    plain = build_runtime_response_payload({"response": "Plain.", "usage": {}, "model": "gpt-6-sol"}, "s-plain")
    assert plain["model"] == "gpt-6-sol"
    assert "requested_model" not in plain
    assert "model" not in build_runtime_response_payload({"response": "Old.", "usage": {}}, "s-old")


def test_response_payload_preserves_markdown_then_file_blocks():
    payload = build_runtime_response_payload(
        {
            "response": "Here is the deck.",
            "usage": {},
            "display_blocks": [{"type": "markdown", "content": "Here is the deck."}, FILE_BLOCK],
        },
        "s-files",
    )

    assert payload["display_blocks"] == [{"type": "markdown", "content": "Here is the deck."}, FILE_BLOCK]


def test_history_message_keeps_file_blocks():
    message = normalize_assistant_history_message(
        {"role": "assistant", "content": "Here is the deck.", "display_blocks": [FILE_BLOCK]}
    )

    assert message["display_blocks"] == [FILE_BLOCK]


def test_file_block_without_path_is_dropped_and_text_falls_back():
    blocks = normalize_display_blocks([{"type": "file", "name": "deck.pptx"}], fallback_text="Here is the deck.")

    assert blocks == [{"type": "markdown", "content": "Here is the deck."}]


def test_file_block_aliases_and_derived_name():
    blocks = normalize_display_blocks(
        [{"type": "FILE ", "file_path": " output/reports/summary.md ", "size": 12.0, "text": "Weekly summary"}]
    )

    assert blocks == [
        {
            "type": "file",
            "path": "output/reports/summary.md",
            "name": "summary.md",
            "size": 12,
            "content": "Weekly summary",
        }
    ]
