from pathlib import Path


def test_jira_attachment_preview_paths_use_shared_helper_and_artifact_contract():
    init_source = Path("src/jira/__init__.py").read_text(encoding="utf-8")
    api_source = Path("src/jira/api.py").read_text(encoding="utf-8")
    helper_source = Path("src/jira/attachment_preview.py").read_text(encoding="utf-8")

    assert "from .attachment_preview import render_issue_attachment_previews" in init_source
    assert "render_issue_attachment_previews(" in init_source
    assert "render_issue_attachment_previews(" in api_source

    assert 'source_type="jira"' in helper_source
    assert 'source_kind="issue_attachment"' in helper_source
    assert "persist_text_ref_session_id=session_id" in helper_source
    assert "artifact_id:" in helper_source
    assert "text_ref:" in helper_source
    assert "parse_status:" in helper_source
