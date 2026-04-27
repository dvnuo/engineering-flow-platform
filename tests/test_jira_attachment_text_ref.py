import pytest

from tests._lightweight_source_service_loaders import load_jira_source_service_lightweight


@pytest.mark.asyncio
async def test_jira_attachment_always_persists_text_ref():
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
        content_format = "text"
        content = "abc"
        artifact_id = "art-1"
        preview = "abc"
        text_ref = "text-1"
        parse_status = "completed"
        parse_error = None
        projected_to_text = True

    module.jira_channel = _Channel()
    module.JiraFormatAdapter = _Adapter

    captured = {}

    def _fake_persist(**kwargs):
        captured["bundle"] = kwargs["bundle"]
        return {"context_ref": "c", "digest_ref": "d", "source_digest_chunk_count": 0}

    module.persist_jira_source_bundle_and_digest = _fake_persist

    async def _fake_download(**kwargs):
        return _Result()

    import sys
    import types

    sys.modules["src.jira"].download_and_process_attachment = _fake_download
    module.artifact_storage.get_artifact = lambda _artifact_id: types.SimpleNamespace(
        artifact_id="art-1",
        file_id="art-1",
        source_type="jira",
        source_kind="issue_attachment",
        filename="a.pdf",
        content_type="application/pdf",
        size=1,
        text_ref="text-1",
    )

    try:
        result = await module.prepare_jira_issue_source("P-1", session_id="s1")
        assert result.bundle["attachments"][0]["text_ref"] == "text-1"
        assert captured["bundle"]["artifact_refs"][0]["text_ref"] == "text-1"
    finally:
        cleanup()
