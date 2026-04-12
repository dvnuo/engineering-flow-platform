"""Utilities for normalizing assistant display blocks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_markdown_display_blocks(text: str) -> List[Dict[str, Any]]:
    """Build a single markdown display block from plain text."""
    if not isinstance(text, str) or not text:
        return []
    return [{"type": "markdown", "content": text}]


def normalize_display_blocks(raw_blocks: Optional[Any], fallback_text: str = "") -> List[Dict[str, Any]]:
    """Return normalized display blocks with markdown fallback.

    Valid blocks are non-empty dict items with a string ``type`` field.
    """
    if isinstance(raw_blocks, list):
        normalized: List[Dict[str, Any]] = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if not isinstance(block_type, str) or not block_type.strip():
                continue
            normalized_block = dict(block)
            normalized_block["type"] = block_type.strip()
            normalized.append(normalized_block)
        if normalized:
            return normalized

    return build_markdown_display_blocks(fallback_text)
