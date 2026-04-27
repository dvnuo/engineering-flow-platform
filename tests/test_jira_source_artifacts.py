import sys
import types

import pytest

from tests._lightweight_source_service_loaders import load_jira_source_service_lightweight


@pytest.mark.asyncio
async def test_jira_bundle_contains_artifact_refs():
    module, cleanup = load_jira_source_service_lightweight()
    try:

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
                    "names": {},
                    "renderedFields": {},
                }

            def _get_comments_list(self, *a, **k):
                return []

            def _convert_description_to_markdown(self, x):
                return ""

            def _extract_acceptance_criteria(self, x):
                return ""

        module.JiraFormatAdapter = _Adapter

        class _Result:
            content_format = "text"
            content = "abc"
            artifact_id = "art-1"
            preview = "abc"
            text_ref = "ctx://text/art-1"
            parse_status = "completed"
            parse_error = None
            projected_to_text = True

        async def _fake_download(**kwargs):
            return _Result()

        module._test_storage.records["art-1"] = types.SimpleNamespace(
            artifact_id="art-1",
            text_ref="ctx://text/art-1",
            context_ref=None,
            digest_ref=None,
        )
        # set downloader/channel through the stubbed src.jira module in sys.modules
        sys.modules["src.jira"].download_and_process_attachment = _fake_download
        sys.modules["src.jira"].jira_channel = types.SimpleNamespace(
            api_version="3",
            _auth_header={},
            is_configured=lambda: True,
            get_instance_client=lambda **kwargs: sys.modules["src.jira"].jira_channel,
        )

        result = await module.prepare_jira_issue_source("P-1", session_id="s1")
        assert "artifact_refs" in result.bundle
        assert "completeness_ledger" in result.bundle
        assert result.bundle["attachments"][0]["text_preview"] == "abc"
        assert result.bundle["attachments"][0]["text_ref"] == "ctx://text/art-1"
        assert result.bundle["artifact_refs"][0]["context_ref"] == "ctx://jira"
    finally:
        cleanup()
