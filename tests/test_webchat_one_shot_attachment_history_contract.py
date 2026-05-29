from pathlib import Path


def test_attachment_context_is_passed_as_runtime_v2_transient_prompt_input():
    webchat = Path("src/gateway/webchat.py").read_text(encoding="utf-8")
    runtime_v2_chat = Path("src/gateway/runtime_v2_chat.py").read_text(encoding="utf-8")

    assert "transient_model_message" in webchat
    assert "transient_model_message" in runtime_v2_chat

    assert "message=history_message" in webchat
    assert "transient_model_message=transient_model_message" in webchat

    api_chat_section = webchat.split("async def api_chat(", 1)[1].split("async def api_chat_stream(", 1)[0]
    run_call_section = api_chat_section.split("_run_chat_via_execution_bus(", 1)[1].split(")", 1)[0]
    assert "message=history_message" in run_call_section
    assert "transient_model_message=transient_model_message" in run_call_section

    compose_section = runtime_v2_chat.split("def _compose_user_prompt(", 1)[1].split("async def _forward_runtime_events", 1)[0]
    assert "transient_model_message" in compose_section
    assert "parts.append(transient)" in compose_section


def test_one_shot_attachment_context_is_represented_by_metadata_not_raw_debug_payloads():
    runtime_v2_chat = Path("src/gateway/runtime_v2_chat.py").read_text(encoding="utf-8")

    metadata_section = runtime_v2_chat.split("def _run_metadata(", 1)[1].split("def _compose_user_prompt(", 1)[0]
    assert '"attached_image_count": len(attached_images or [])' in metadata_section
    assert '"has_transient_model_message": bool(transient_model_message)' in metadata_section
    assert '"attachments": list(attachments or [])' in metadata_section

    debug_section = runtime_v2_chat.split('"_llm_debug": {', 1)[1].split("}", 2)[0]
    assert '"provider": "github-copilot"' in debug_section
    assert '"model": model' in debug_section
    assert "transient_model_message" not in debug_section
    assert "attached_images" not in debug_section


def test_context_file_and_chunk_search_routes_do_not_expose_one_shot_upload_context():
    source = Path("src/gateway/webchat.py").read_text(encoding="utf-8")

    context_files_section = source.split("async def api_context_files(", 1)[1].split("async def api_chunks_search(", 1)[0]
    assert "get_session_files" not in context_files_section
    assert "'files': []" in context_files_section or '"files": []' in context_files_section

    chunks_section = source.split("async def api_chunks_search(", 1)[1].split("async def", 1)[0]
    assert "retrieval_engine.retrieve" not in chunks_section
    assert "'chunks': []" in chunks_section or '"chunks": []' in chunks_section
    assert "'total': 0" in chunks_section or '"total": 0' in chunks_section
