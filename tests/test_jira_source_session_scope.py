import pytest


@pytest.mark.asyncio
async def test_prepare_jira_issue_source_requires_real_session_for_persist(monkeypatch):
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
                    "attachment": [],
                },
                "names": {},
            }

        def _get_comments_list(self, *args, **kwargs):
            return []

        def _convert_description_to_markdown(self, _value):
            return ""

        def _extract_acceptance_criteria(self, _issue):
            return ""

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.source_service.JiraFormatAdapter", _Adapter)

    called = {"n": 0}

    def _fake_persist(**kwargs):
        called["n"] += 1
        return {"context_ref": "ctx://context/s1/jira_source_bundle/a", "digest_ref": "ctx://context/s1/jira_source_digest/a", "source_digest_chunk_count": 0}

    monkeypatch.setattr("src.jira.source_service.persist_jira_source_bundle_and_digest", _fake_persist)

    without_session = await prepare_jira_issue_source("P-1", session_id=None)
    assert without_session.manifest["context_ref"] is None
    assert without_session.manifest["digest_ref"] is None
    assert "session_scope_missing" in without_session.bundle["completeness_ledger"]["partial_reasons"]

    with_session = await prepare_jira_issue_source("P-1", session_id="s1")
    assert called["n"] == 1
    assert with_session.manifest["context_ref"] is not None
    assert with_session.manifest["digest_ref"] is not None
