from pathlib import Path


def test_attachment_rag_context_is_transient_not_history_message():
    webchat = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    core = Path("src/agents/core.py").read_text(encoding="utf-8")

    assert "transient_model_message" in webchat
    assert "transient_model_message" in core

    assert "message=history_message" in webchat
    assert "transient_model_message=transient_model_message" in webchat

    run_section = webchat.split("execute_chat_orchestration(", 1)[1]
    input_payload_section = run_section.split("metadata={", 1)[0]
    assert "transient_model_message" not in input_payload_section

    process_section = core.split("async def process(", 1)[1].split("def emit_early_runtime_event", 1)[0]
    assert "session_manager.add_message(" in process_section
    add_call = process_section.split("session_manager.add_message(", 1)[1].split(")", 1)[0]
    assert "message" in add_call
    assert "transient_model_message" not in add_call

    assert "Applied transient model-only message" in core
    assert 'replaced["content"] = transient_model_message' in core or "replaced['content'] = transient_model_message" in core


def test_one_shot_attachment_context_is_not_saved_in_debug_or_runtime_events():
    webchat = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    core = Path("src/agents/core.py").read_text(encoding="utf-8")

    assert "ONE_SHOT_ATTACHMENT_REDACTION" in core
    assert "_has_one_shot_attachment_context" in core
    assert "_redact_one_shot_attachment_context_state" in core
    assert "_redact_one_shot_attachment_llm_debug" in core

    assert "one_shot_attachment_context_active" in core
    assert "_redact_one_shot_attachment_context_state(context_state)" in core
    assert "_redact_one_shot_attachment_llm_debug" in core

    assert "if one_shot_attachment_context_active:" in core
    assert "safe_preview(message, 200)" in core

    assert 'llm_result["_llm_debug"] = _redact_one_shot_attachment_llm_debug' in core or "llm_result['_llm_debug'] = _redact_one_shot_attachment_llm_debug" in core

    run_section = webchat.split("execute_chat_orchestration(", 1)[1]
    input_payload_section = run_section.split("metadata={", 1)[0]

    assert "transient_model_message" not in input_payload_section
    assert '"attached_images": attached_images' not in input_payload_section
    assert "'attached_images': attached_images" not in input_payload_section
