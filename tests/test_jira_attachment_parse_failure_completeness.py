import pytest


@pytest.mark.asyncio
async def test_jira_projectable_attachment_parse_failure_lowers_completeness(monkeypatch):
    from src.jira.source_service import prepare_jira_issue_source

    class _Channel:
        api_version = "3"
        _auth_header = {}

        def is_configured(self):
            return True

        def get_instance_client(self, **kwargs):
            return self

    class _Adapter:
        def __init__(self, _):
            pass

        async def get_issue(self, **kwargs):
            return {
                "key": "P-1",
                "fields": {
                    "summary": "S",
                    "comment": {"comments": [], "total": 0},
                    "attachment": [{"id": "1", "filename": "a.pdf", "mimeType": "application/pdf", "content": "u"}],
                },
            }

        def _get_comments_list(self, *a, **k):
            return []

        def _convert_description_to_markdown(self, x):
            return ""

        def _extract_acceptance_criteria(self, x):
            return ""

    class _Result:
        content_format = "metadata"
        content = "[application/pdf: a.pdf]"
        artifact_id = "art-1"
        preview = None
        text_ref = None
        parse_status = "failed"
        parse_error = "parse failed"
        projected_to_text = False

    async def _fake_download(**kwargs):
        return _Result()

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.source_service.JiraFormatAdapter", _Adapter)
    monkeypatch.setattr("src.jira.download_and_process_attachment", _fake_download)

    monkeypatch.setattr(
        "src.jira.source_service.persist_jira_source_bundle_and_digest",
        lambda **kwargs: {"context_ref": "c", "digest_ref": "d", "source_digest_chunk_count": 0},
    )

    out = await prepare_jira_issue_source("P-1", session_id="s1")
    ledger = out.bundle["completeness_ledger"]

    assert ledger["text_attachments_loaded"] == 0
    assert ledger["text_attachment_bodies_complete"] is False
    assert ledger["source_complete_for_generation"] is False
    assert ledger["source_complete"] is False
    assert any(str(r).startswith("attachment_text_processing_failed:a.pdf:parse_failed") for r in ledger["attachment_body_partial_reasons"])
