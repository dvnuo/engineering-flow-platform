"""Pure helpers for normalizing WebChat assistant payloads."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_normalize_display_blocks_fn: Optional[Callable[[Optional[Any], str], list[dict[str, Any]]]] = None


def _get_normalize_display_blocks() -> Callable[[Optional[Any], str], list[dict[str, Any]]]:
    """Resolve ``normalize_display_blocks`` with an import-light fallback."""
    global _normalize_display_blocks_fn
    if _normalize_display_blocks_fn is not None:
        return _normalize_display_blocks_fn

    try:
        from src.runtime.display_blocks import normalize_display_blocks as runtime_normalize_display_blocks
        _normalize_display_blocks_fn = runtime_normalize_display_blocks
        return _normalize_display_blocks_fn
    except Exception:
        module_path = Path(__file__).resolve().parent.parent / "runtime" / "display_blocks.py"
        spec = importlib.util.spec_from_file_location("runtime_display_blocks_fallback", module_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Failed to load display_blocks module from {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        normalize_display_blocks = getattr(module, "normalize_display_blocks", None)
        if normalize_display_blocks is None or not callable(normalize_display_blocks):
            raise ImportError("normalize_display_blocks not found in fallback module")
        _normalize_display_blocks_fn = normalize_display_blocks
        return _normalize_display_blocks_fn


def _meaningful_text(value: Any) -> str:
    """Return original text when meaningful; otherwise empty string."""
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    return text


def build_webchat_response_payload(result: Optional[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
    """Build the stable JSON payload returned by ``/api/chat``."""
    payload_result = result if isinstance(result, dict) else {}
    response_value = _meaningful_text(payload_result.get("response"))
    content_value = _meaningful_text(payload_result.get("content"))
    response_text = response_value or content_value
    usage = payload_result.get("usage", {}) or {}
    normalize_display_blocks = _get_normalize_display_blocks()

    response_payload: Dict[str, Any] = {
        "response": response_text,
        "session_id": session_id,
        "usage": usage,
        "display_blocks": normalize_display_blocks(payload_result.get("display_blocks"), response_text),
    }
    request_id = payload_result.get("request_id")
    if not request_id:
        execution_result = payload_result.get("_execution_result")
        request_id = getattr(execution_result, "request_id", None) if execution_result is not None else None
    if request_id:
        response_payload["request_id"] = request_id
    for key in (
        "user_message_id",
        "events",
        "context_state",
        "_llm_debug",
        "reasoning",
        "author_type",
        "author_id",
        "author_name",
        "author_source",
    ):
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
    normalize_display_blocks = _get_normalize_display_blocks()
    fallback_text = _meaningful_text(message.get("content"))
    normalized_message["display_blocks"] = normalize_display_blocks(
        message.get("display_blocks"),
        fallback_text,
    )
    return normalized_message
