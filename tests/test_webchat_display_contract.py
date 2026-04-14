"""Import-light contract tests for WebChat payload normalization helpers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess


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


def test_webchat_source_includes_display_blocks_final_assistant_checks():
    repo_root = Path(__file__).parent.parent
    js_source = (repo_root / "src" / "gateway" / "static" / "js" / "webchat.js").read_text(encoding="utf-8")

    assert "function getBlockText(block, preferCode = false)" in js_source
    assert "lastMsg.display_blocks" in js_source
    assert "hasMeaningfulDisplayBlocks(lastMsg.display_blocks)" in js_source


def test_build_webchat_response_payload_treats_whitespace_response_as_empty():
    mod = _load_chat_payloads_module()
    payload = mod.build_webchat_response_payload(
        {"response": "   ", "display_blocks": [None, {"type": "   "}]},
        "s-4",
    )

    assert payload["response"] == ""
    assert payload["display_blocks"] == []


def test_build_webchat_response_payload_preserves_raw_markdown_newlines():
    mod = _load_chat_payloads_module()
    raw_markdown = "\n# Title\n\nBody\n"
    payload = mod.build_webchat_response_payload({"response": raw_markdown}, "s-raw")

    assert payload["response"] == raw_markdown
    assert payload["display_blocks"] == [{"type": "markdown", "content": raw_markdown}]


def test_build_webchat_response_payload_preserves_indented_markdown_whitespace():
    mod = _load_chat_payloads_module()
    raw_markdown = "    indented code line\n    second\n"
    payload = mod.build_webchat_response_payload({"response": raw_markdown}, "s-indent")

    assert payload["response"] == raw_markdown
    assert payload["display_blocks"] == [{"type": "markdown", "content": raw_markdown}]


def test_build_webchat_response_payload_backfills_request_id_from_execution_result():
    mod = _load_chat_payloads_module()
    execution_result = type("ExecutionResult", (), {"request_id": "exec-123"})()
    payload = mod.build_webchat_response_payload(
        {"response": "hello", "_execution_result": execution_result},
        "s-request-id",
    )

    assert payload["request_id"] == "exec-123"


def test_normalize_assistant_history_message_treats_whitespace_content_as_empty():
    mod = _load_chat_payloads_module()
    message = mod.normalize_assistant_history_message(
        {"role": "assistant", "content": "   ", "display_blocks": [None, {"type": "   "}]}
    )

    assert message["display_blocks"] == []


def test_render_single_display_block_uses_non_blank_output_text():
    repo_root = Path(__file__).parent.parent
    js_path = repo_root / "src" / "gateway" / "static" / "js" / "webchat.js"
    js_source = js_path.read_text(encoding="utf-8")

    get_block_text_start = js_source.find("function getBlockText(block, preferCode = false)")
    render_single_start = js_source.find("function renderSingleDisplayBlock(block)")
    render_code_start = js_source.find("function renderCodeBlock(block)")
    assert get_block_text_start != -1
    assert render_single_start != -1
    assert render_code_start != -1

    get_block_text_fn = js_source[get_block_text_start:render_single_start]
    render_single_fn = js_source[render_single_start:render_code_start]

    script = f"""
{get_block_text_fn}
function escapeHtml(v) {{ return String(v); }}
function renderMarkdown(v) {{ return String(v); }}
{render_single_fn}
const html = renderSingleDisplayBlock({json.dumps({"type": "tool_result", "content": "   ", "output": "done"})});
console.log(html);
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert "done" in result.stdout


def test_render_code_block_ignores_blank_code_and_uses_text_fallback():
    repo_root = Path(__file__).parent.parent
    js_path = repo_root / "src" / "gateway" / "static" / "js" / "webchat.js"
    js_source = js_path.read_text(encoding="utf-8")

    get_block_text_start = js_source.find("function getBlockText(block, preferCode = false)")
    render_single_start = js_source.find("function renderSingleDisplayBlock(block)")
    render_code_start = js_source.find("function renderCodeBlock(block)")
    render_table_start = js_source.find("function renderTableBlock(block)")
    assert get_block_text_start != -1
    assert render_single_start != -1
    assert render_code_start != -1
    assert render_table_start != -1

    get_block_text_fn = js_source[get_block_text_start:render_single_start]
    render_code_fn = js_source[render_code_start:render_table_start]

    script = f"""
{get_block_text_fn}
function escapeHtml(v) {{ return String(v); }}
{render_code_fn}
const html = renderCodeBlock({json.dumps({"type": "code", "code": "   ", "text": "print(1)", "language": "python"})});
console.log(html);
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert "print(1)" in result.stdout
    assert "language-python" in result.stdout


def test_has_final_assistant_ignores_shell_display_blocks():
    repo_root = Path(__file__).parent.parent
    js_path = repo_root / "src" / "gateway" / "static" / "js" / "webchat.js"
    js_source = js_path.read_text(encoding="utf-8")

    parse_start = js_source.find("function parseDisplayBlocks(raw)")
    get_block_text_start = js_source.find("function getBlockText(block, preferCode = false)")
    has_meaningful_start = js_source.find("function hasMeaningfulDisplayBlocks(blocks)")
    render_single_start = js_source.find("function renderSingleDisplayBlock(block)")
    has_final_start = js_source.find("function hasFinalAssistant(sessionData)")
    poll_start = js_source.find("async function pollSessionUntilFinal()")
    assert parse_start != -1
    assert get_block_text_start != -1
    assert has_meaningful_start != -1
    assert render_single_start != -1
    assert has_final_start != -1
    assert poll_start != -1

    parse_fn = js_source[parse_start:get_block_text_start]
    get_block_text_fn = js_source[get_block_text_start:has_meaningful_start]
    has_meaningful_fn = js_source[has_meaningful_start:render_single_start]
    has_final_fn = js_source[has_final_start:poll_start]

    session_payload = {
        "messages": [
            {"role": "assistant", "content": "   ", "display_blocks": [{"type": "tool_result", "content": "   "}]}
        ]
    }
    script = f"""
{parse_fn}
{get_block_text_fn}
{has_meaningful_fn}
{has_final_fn}
const result = hasFinalAssistant({json.dumps(session_payload)});
console.log(String(result));
"""
    result = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    assert result.stdout.strip() == "false"
