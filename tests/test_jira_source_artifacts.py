import pytest


@pytest.mark.asyncio
async def test_jira_bundle_contains_artifact_refs(monkeypatch):
    from src.jira.source_service import prepare_jira_issue_source

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self

    class _Adapter:
        def __init__(self, _): pass
        async def get_issue(self, **kwargs):
            return {"key": "P-1", "fields": {"summary": "S", "comment": {"comments": [], "total": 0}, "attachment": [{"id": "1", "filename": "a.pdf", "mimeType": "application/pdf", "content": "u"}]}}
        def _get_comments_list(self, *a, **k): return []
        def _convert_description_to_markdown(self, x): return ""
        def _extract_acceptance_criteria(self, x): return ""

    class _Result:
        content_format = "text"
        content = "abc"
        artifact_id = "art-1"
        preview = "abc"

    monkeypatch.setattr("src.jira.source_service.persist_jira_source_bundle_and_digest", lambda **kwargs: {"context_ref": "c", "digest_ref": "d", "source_digest_chunk_count": 0})
    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.source_service.JiraFormatAdapter", _Adapter)
    
    async def _fake_download(**kwargs):
        return _Result()
    monkeypatch.setattr("src.jira.download_and_process_attachment", _fake_download)
    result = await prepare_jira_issue_source("P-1")
    assert "artifact_refs" in result.bundle
    assert "completeness_ledger" in result.bundle
    assert "text_preview" in result.bundle["attachments"][0]
