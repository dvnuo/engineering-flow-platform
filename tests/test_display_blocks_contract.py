"""Focused display_blocks contract tests with minimal import surface."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_display_blocks_module():
    repo_root = Path(__file__).parent.parent
    module_path = repo_root / "src" / "runtime" / "display_blocks.py"
    spec = importlib.util.spec_from_file_location("display_blocks_contract", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_normalize_code_aliases_to_canonical_fields():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "code", "text": "print(1)", "language": "python"}
    ])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "code"
    assert blocks[0]["content"] == "print(1)"
    assert blocks[0]["lang"] == "python"


def test_normalize_table_columns_to_headers():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "table", "columns": ["A"], "rows": [["1"]]}
    ])

    assert len(blocks) == 1
    assert blocks[0]["headers"] == ["A"]
    assert blocks[0]["rows"] == [["1"]]


def test_normalize_invalid_blocks_falls_back_to_markdown():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks(
        [{"type": "   "}, "bad", None],
        fallback_text="hello",
    )

    assert blocks == [{"type": "markdown", "content": "hello"}]


def test_render_code_block_uses_block_text_fallback():
    repo_root = Path(__file__).parent.parent
    js = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    anchor = "function renderCodeBlock(block)"
    start = js.find(anchor)
    assert start != -1
    chunk = js[start:start + 500]

    assert "getBlockText(block)" in chunk or "block.text" in chunk


def test_normalize_tool_result_output_alias_to_content():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "tool_result", "title": "Bash", "status": "success", "output": "done"}
    ])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["content"] == "done"


def test_normalize_callout_message_alias_to_content():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "callout", "title": "Note", "message": "hello"}
    ])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "callout"
    assert blocks[0]["content"] == "hello"


def test_build_markdown_display_blocks_skips_whitespace_only_text():
    mod = _load_display_blocks_module()

    assert mod.build_markdown_display_blocks("   ") == []
    assert mod.build_markdown_display_blocks("\n\n") == []


def test_normalize_tool_result_uses_non_blank_output_when_content_blank():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "tool_result", "content": "   ", "output": "done"}
    ])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["content"] == "done"


def test_normalize_code_uses_non_blank_text_when_content_blank():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks([
        {"type": "code", "content": "  ", "text": "print(1)"}
    ])

    assert len(blocks) == 1
    assert blocks[0]["type"] == "code"
    assert blocks[0]["content"] == "print(1)"


def test_normalize_whitespace_tool_result_is_dropped_without_fallback():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks(
        [{"type": "tool_result", "title": "Bash", "content": "   "}],
        fallback_text="",
    )

    assert blocks == []


def test_normalize_whitespace_callout_uses_markdown_fallback():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks(
        [{"type": "callout", "title": "Note", "content": "   "}],
        fallback_text="fallback",
    )

    assert blocks == [{"type": "markdown", "content": "fallback"}]


def test_normalize_code_prefers_non_blank_text_when_code_blank():
    mod = _load_display_blocks_module()

    blocks = mod.normalize_display_blocks(
        [{"type": "code", "code": "   ", "text": "print(1)", "language": "python"}]
    )

    assert len(blocks) == 1
    assert blocks[0]["type"] == "code"
    assert blocks[0]["content"] == "print(1)"
    assert blocks[0]["lang"] == "python"
