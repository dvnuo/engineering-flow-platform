"""Pure helpers for normalizing runtime chat assistant payloads."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_normalize_display_blocks_fn: Optional[Callable[[Optional[Any], str], list[dict[str, Any]]]] = None


def _get_normalize_display_blocks() -> Callable[[Optional[Any], str], list[dict[str, Any]]]:
    """Resolve the gateway-local display block normalizer."""
    global _normalize_display_blocks_fn
    if _normalize_display_blocks_fn is not None:
        return _normalize_display_blocks_fn
    _normalize_display_blocks_fn = normalize_display_blocks
    return _normalize_display_blocks_fn


def build_markdown_display_blocks(text: str) -> list[dict[str, Any]]:
    """Build a single markdown display block from plain text."""
    if not isinstance(text, str) or not text.strip():
        return []
    return [{"type": "markdown", "content": text}]


def _first_text_value(block: dict[str, Any], field_order: tuple[str, ...]) -> str:
    for key in field_order:
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _text_value(block: dict[str, Any]) -> str:
    return _first_text_value(
        block,
        ("content", "text", "message", "output", "result", "value"),
    )


def _normalize_display_block(block: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    if not isinstance(block_type, str) or not block_type.strip():
        return None
    normalized_type = block_type.strip().lower()

    if normalized_type == "markdown":
        content = _text_value(block)
        return {"type": "markdown", "content": content} if content else None

    if normalized_type == "callout":
        content = _text_value(block)
        if not content:
            return None
        normalized_block: dict[str, Any] = {"type": "callout", "content": content}
        for source_key in ("title", "tone"):
            value = _first_text_value(block, (source_key,))
            if value:
                normalized_block[source_key] = value
        return normalized_block

    if normalized_type == "tool_result":
        content = _text_value(block)
        if not content:
            return None
        normalized_block = {"type": "tool_result", "content": content}
        for source_key in ("title", "status"):
            value = _first_text_value(block, (source_key,))
            if value:
                normalized_block[source_key] = value
        return normalized_block

    if normalized_type == "code":
        content = _first_text_value(
            block,
            ("content", "code", "text", "output", "result", "value"),
        )
        if not content:
            return None
        normalized_block = {"type": "code", "content": content}
        language = block.get("lang") if block.get("lang") is not None else block.get("language")
        if isinstance(language, str) and language.strip():
            normalized_block["lang"] = language
        return normalized_block

    if normalized_type == "table":
        headers = block.get("headers")
        if not isinstance(headers, list):
            headers = block.get("columns")
        rows = block.get("rows")
        normalized_headers = headers if isinstance(headers, list) else []
        normalized_rows = rows if isinstance(rows, list) else []
        content = _text_value(block)
        if not normalized_headers and not normalized_rows and not content:
            return None
        if not normalized_headers and not normalized_rows and content:
            return {"type": "markdown", "content": content}
        normalized_block = {
            "type": "table",
            "headers": normalized_headers,
            "rows": normalized_rows,
        }
        if content:
            normalized_block["content"] = content
        return normalized_block

    content = _text_value(block)
    return {"type": normalized_type, "content": content} if content else None


def normalize_display_blocks(raw_blocks: Optional[Any], fallback_text: str = "") -> list[dict[str, Any]]:
    """Return normalized display blocks with markdown fallback."""
    if isinstance(raw_blocks, list):
        normalized = [
            normalized_block
            for block in raw_blocks
            if (normalized_block := _normalize_display_block(block)) is not None
        ]
        if normalized:
            return normalized
    return build_markdown_display_blocks(fallback_text)


def _meaningful_text(value: Any) -> str:
    """Return original text when meaningful; otherwise empty string."""
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    return text


def build_runtime_response_payload(result: Optional[Dict[str, Any]], session_id: str) -> Dict[str, Any]:
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
        "runtime_events",
        "status",
        "error",
        "error_type",
        "details",
        "_llm_debug",
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
