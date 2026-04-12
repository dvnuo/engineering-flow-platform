"""Pure helpers for normalizing WebChat assistant payloads."""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.runtime.display_blocks import normalize_display_blocks


def build_webchat_response_payload(result: Optional[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
    """Build the stable JSON payload returned by ``/api/chat``."""
    payload_result = result if isinstance(result, dict) else {}
    response_text = payload_result.get("response") or payload_result.get("content") or ""
    usage = payload_result.get("usage", {}) or {}

    response_payload: Dict[str, Any] = {
        "response": response_text,
        "session_id": session_id,
        "usage": usage,
        "display_blocks": normalize_display_blocks(payload_result.get("display_blocks"), response_text),
    }
    for key in ("user_message_id", "events", "_llm_debug", "reasoning"):
        if key in payload_result:
            response_payload[key] = payload_result[key]
    return response_payload


def normalize_assistant_history_message(message: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow-copied assistant message with normalized display blocks."""
    if not isinstance(message, dict):
        return message
    normalized_message = dict(message)
    if normalized_message.get("role") != "assistant":
        return normalized_message
    normalized_message["display_blocks"] = normalize_display_blocks(
        message.get("display_blocks"),
        message.get("content", "") or "",
    )
    return normalized_message
