from pathlib import Path


def test_confluence_attachment_preview_path_uses_session_scoped_artifact_contract():
    source = Path("src/confluence/__init__.py").read_text(encoding="utf-8")

    assert "async def _process_confluence_attachments(" in source
    assert "session_id: Optional[str] = None" in source
    assert "session_id=None" not in source
    assert 'source_type="confluence"' in source
    assert 'source_kind="page_attachment"' in source
    assert "persist_text_ref_session_id=session_id" in source
    assert "artifact_id:" in source
    assert "text_ref:" in source
    assert "parse_status:" in source
