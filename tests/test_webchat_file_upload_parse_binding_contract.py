from pathlib import Path


def test_webchat_upload_parse_binding_source_contract():
    source = Path("src/gateway/webchat.py").read_text(encoding="utf-8")

    assert "async def api_files_upload(" in source
    assert "add_file_to_session(" in source
    assert "register_existing_file_as_artifact(" in source
    assert "bind_artifact_to_session(" in source

    assert "async def api_files_parse(" in source
    assert 'update_file_status(session_id, file_id, status="processing")' in source
    assert "update_projection_from_parse_result(" in source
    assert "retrieval_engine.rebuild_index(session_id)" in source
    assert 'update_file_status(session_id, file_id, status="failed"' in source
    assert 'parse_status="failed"' in source
    assert "model_dump(" in source
