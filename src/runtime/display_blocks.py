"""Utilities for normalizing assistant display blocks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_markdown_display_blocks(text: str) -> List[Dict[str, Any]]:
    """Build a single markdown display block from plain text."""
    if not isinstance(text, str) or not text:
        return []
    return [{"type": "markdown", "content": text}]


def _text_value(block: Dict[str, Any]) -> str:
    return str(block.get("content") if block.get("content") is not None else (block.get("text") or ""))


def _normalize_display_block(block: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    if not isinstance(block_type, str):
        return None
    normalized_type = block_type.strip().lower()
    if not normalized_type:
        return None

    normalized_block: Dict[str, Any] = dict(block)
    normalized_block["type"] = normalized_type

    if normalized_type in {"markdown", "callout", "tool_result"}:
        normalized_block["content"] = _text_value(block)
        return normalized_block

    if normalized_type == "code":
        code_text = block.get("content")
        if code_text is None:
            code_text = block.get("code")
        if code_text is None:
            code_text = block.get("text")
        normalized_block["content"] = "" if code_text is None else str(code_text)
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
        normalized_block["headers"] = headers if isinstance(headers, list) else []
        rows = block.get("rows")
        normalized_block["rows"] = rows if isinstance(rows, list) else []
        normalized_block["content"] = _text_value(block)
        return normalized_block

    return normalized_block


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
