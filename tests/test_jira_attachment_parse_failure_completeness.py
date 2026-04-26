import pytest

from tests._lightweight_source_service_loaders import load_jira_source_service_lightweight


@pytest.mark.asyncio
async def test_jira_projectable_attachment_parse_failure_lowers_completeness():
    module, cleanup = load_jira_source_service_lightweight()
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

    module.jira_channel = _Channel()
    module.JiraFormatAdapter = _Adapter
    module.persist_jira_source_bundle_and_digest = (
        lambda **kwargs: {"context_ref": "c", "digest_ref": "d", "source_digest_chunk_count": 0}
    )

    import sys

    sys.modules["src.jira"].download_and_process_attachment = _fake_download

    try:
        out = await module.prepare_jira_issue_source("P-1", session_id="s1")
        ledger = out.bundle["completeness_ledger"]

        assert ledger["text_attachments_loaded"] == 0
        assert ledger["text_attachment_bodies_complete"] is False
        assert ledger["source_complete_for_generation"] is False
        assert ledger["source_complete"] is False
        assert any(str(r).startswith("attachment_text_processing_failed:a.pdf:parse_failed") for r in ledger["attachment_body_partial_reasons"])
    finally:
        cleanup()
