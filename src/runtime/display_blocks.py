"""Utilities for normalizing assistant display blocks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_markdown_display_blocks(text: str) -> List[Dict[str, Any]]:
    """Build a single markdown display block from plain text."""
    if not isinstance(text, str):
        return []
    if not text.strip():
        return []
    return [{"type": "markdown", "content": text}]


def _first_text_value(block: Dict[str, Any], field_order: tuple[str, ...]) -> str:
    for field_name in field_order:
        value = block.get(field_name)
        if value is None:
            continue
        text = str(value)
        if not text.strip():
            continue
        return text
    return ""


def _text_value(block: Dict[str, Any]) -> str:
    return _first_text_value(
        block,
        ("content", "text", "message", "output", "result", "value"),
    )


def _normalize_display_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    if not isinstance(block_type, str):
        return None
    normalized_type = block_type.strip().lower()
    if not normalized_type:
        return None

    if normalized_type == "markdown":
        content = _text_value(block)
        if not content:
            return None
        return {"type": "markdown", "content": content}

    if normalized_type == "callout":
        content = _text_value(block)
        if not content:
            return None
        normalized_block: Dict[str, Any] = {"type": "callout", "content": content}
        title = _first_text_value(block, ("title",))
        tone = _first_text_value(block, ("tone",))
        if title:
            normalized_block["title"] = title
        if tone:
            normalized_block["tone"] = tone
        return normalized_block

    if normalized_type == "tool_result":
        content = _text_value(block)
        if not content:
            return None
        normalized_block = {"type": "tool_result", "content": content}
        title = _first_text_value(block, ("title",))
        status = _first_text_value(block, ("status",))
        if title:
            normalized_block["title"] = title
        if status:
            normalized_block["status"] = status
        return normalized_block

    if normalized_type == "code":
        content = _first_text_value(
            block,
            ("content", "code", "text", "value", "output"),
        )
        if not content:
            return None
        normalized_block = {"type": "code", "content": content}
        language = block.get("lang")
        if language is None:
            language = block.get("language")
        if language is not None:
            normalized_block["lang"] = str(language)
        return normalized_block

    if normalized_type == "table":
        headers = block.get("headers")
        if not isinstance(headers, list):
            headers = block.get("columns")
        normalized_headers = headers if isinstance(headers, list) else []
        rows = block.get("rows")
        normalized_rows = rows if isinstance(rows, list) else []
        content = _text_value(block)
        if not normalized_headers and not normalized_rows and not content:
            return None
        normalized_block = {
            "type": "table",
            "headers": normalized_headers,
            "rows": normalized_rows,
        }
        if content:
            normalized_block["content"] = content
        return normalized_block

    content = _text_value(block)
    if not content:
        return None
    return {"type": "markdown", "content": content}


def normalize_display_blocks(raw_blocks: Optional[Any], fallback_text: str = "") -> List[Dict[str, Any]]:
    """Return normalized display blocks with markdown fallback.

    Valid blocks are non-empty dict items with a string ``type`` field.
    """
    if isinstance(raw_blocks, list):
        normalized: List[Dict[str, Any]] = []
        for block in raw_blocks:
            normalized_block = _normalize_display_block(block)
            if normalized_block is not None:
                normalized.append(normalized_block)
        if normalized:
            return normalized

    return build_markdown_display_blocks(fallback_text)
