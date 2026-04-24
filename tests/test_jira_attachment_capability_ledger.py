import pytest


@pytest.mark.asyncio
async def test_jira_attachment_binary_skipped_respects_capability(monkeypatch):
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
                    "attachment": [
                        {"id": "1", "filename": "a.pdf", "mimeType": "application/pdf"},
                        {"id": "2", "filename": "a.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                        {"id": "3", "filename": "a.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
                        {"id": "4", "filename": "a.png", "mimeType": "image/png"},
                    ],
                },
            }

        def _get_comments_list(self, *args, **kwargs):
            return []

        def _convert_description_to_markdown(self, _value):
            return ""

        def _extract_acceptance_criteria(self, _issue):
            return ""

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.source_service.JiraFormatAdapter", _Adapter)
    monkeypatch.setattr(
        "src.jira.source_service.persist_jira_source_bundle_and_digest",
        lambda **kwargs: {"context_ref": "c", "digest_ref": "d", "source_digest_chunk_count": 0},
    )

    result = await prepare_jira_issue_source("P-1", include_attachments=False, session_id="s1")
    reasons = result.bundle["completeness_ledger"]["attachment_body_partial_reasons"]

    assert all("a.pdf" not in r for r in reasons)
    assert all("a.docx" not in r for r in reasons)
    assert all("a.xlsx" not in r for r in reasons)
    assert any("a.png" in r for r in reasons)
