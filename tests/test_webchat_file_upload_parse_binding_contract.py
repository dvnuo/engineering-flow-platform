from pathlib import Path


def test_webchat_upload_parse_binding_source_contract():
    source = Path("src/gateway/webchat.py").read_text(encoding="utf-8")

    assert "async def api_files_upload(" in source
    upload_section = source.split("async def api_files_upload(", 1)[1].split("async def api_files_parse(", 1)[0]
    assert "register_existing_file_as_artifact(" not in upload_section
    assert "bind_artifact_to_session(" not in upload_section
    assert "add_file_to_session(" not in upload_section

    parse_section = source.split("async def api_files_parse(", 1)[1].split("async def api_files_preview(", 1)[0]
    assert "register_existing_file_as_artifact(" not in parse_section
    assert "bind_artifact_to_session(" not in parse_section
    assert "update_projection_from_parse_result(" not in parse_section
    assert "artifact_storage.update_artifact_status" not in parse_section
    assert "file_context_storage.add_file_to_session" in parse_section
    assert "file_context_storage.save_chunk" in parse_section
    assert "retrieval_engine.rebuild_index(session_id)" in parse_section

    list_section = source.split("async def api_files_list(", 1)[1].split("async def api_context_files(", 1)[0]
    assert "list_files(" not in list_section
