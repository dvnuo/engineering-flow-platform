import pytest


@pytest.mark.asyncio
async def test_issue_and_pr_sources_materialize_assets_and_session_scope(monkeypatch):
    try:
        from src.github import source_service
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"github source service import unavailable in this environment: {exc}")

    async def _fake_get_issue(owner, repo, issue_number):
        return {"id": 1, "number": issue_number, "title": "Issue", "state": "open", "body": "see https://github.com/user-attachments/assets/issue1"}

    async def _fake_get_issue_comments(owner, repo, issue_number):
        return [{"id": 99, "body": "comment asset https://github.com/user-attachments/assets/c1"}]

    async def _fake_get_pr(owner, repo, pull_number):
        return {"id": 2, "number": pull_number, "title": "PR", "state": "open", "body": "pr body https://github.com/user-attachments/assets/p1"}

    async def _fake_get_pr_comments(owner, repo, pull_number):
        return [{"id": 77, "body": "review https://github.com/user-attachments/assets/r1"}]

    monkeypatch.setattr(source_service.github_channel, "get_issue", _fake_get_issue)
    monkeypatch.setattr(source_service.github_channel, "get_issue_comments", _fake_get_issue_comments)
    monkeypatch.setattr(source_service.github_channel, "get_pull_request", _fake_get_pr)
    monkeypatch.setattr(source_service.github_channel, "get_pr_comments", _fake_get_pr_comments)

    calls = []

    class _FakeResult:
        def __init__(self, idx: int):
            self.file_id = f"f{idx}"
            self.content_type = "text/plain"
            self.content = "x"
            self.content_format = "text"
            self.filename = f"a{idx}.txt"
            self.metadata = {}
            self.artifact_id = f"art-{idx}"
            self.projection_kind = "markdown"
            self.preview = "preview"
            self.text_ref = f"ctx://text/{idx}"
            self.parse_status = "completed"
            self.parse_error = None
            self.projected_to_text = True

    async def _fake_download_and_process_attachment(**kwargs):
        calls.append(kwargs)
        return _FakeResult(len(calls))

    monkeypatch.setattr(source_service, "download_and_process_attachment", _fake_download_and_process_attachment)

    monkeypatch.setattr(source_service, "build_artifact_ref_dict", lambda record: {"artifact_id": record.artifact_id, "text_ref": record.text_ref})

    class _Rec:
        def __init__(self, artifact_id):
            self.artifact_id = artifact_id
            self.text_ref = f"ctx://artifact/{artifact_id}"

    monkeypatch.setattr(source_service.artifact_storage, "get_artifact", lambda artifact_id: _Rec(artifact_id))

    persisted_calls = []

    def _fake_persist(**kwargs):
        persisted_calls.append(kwargs)
        return {"context_ref": "ctx://bundle/s1", "digest_ref": "ctx://digest/s1", "source_complete": True}

    monkeypatch.setattr(source_service, "persist_github_source_bundle_and_digest", _fake_persist)

    issue_with_session = await source_service.prepare_github_issue_source("acme", "repo", 123, session_id="s1")
    assert issue_with_session["bundle"]["context_ref"] == "ctx://bundle/s1"
    assert issue_with_session["bundle"]["artifact_refs"]
    assert issue_with_session["bundle"]["completeness_ledger"]["asset_entries_created"] >= 1

    issue_no_session = await source_service.prepare_github_issue_source("acme", "repo", 123, session_id=None)
    assert issue_no_session["bundle"]["context_ref"] is None
    assert "session_scope_missing" in issue_no_session["bundle"]["completeness_ledger"]["partial_reasons"]

    pr_with_session = await source_service.prepare_github_pr_source("acme", "repo", 456, session_id="s1")
    assert pr_with_session["bundle"]["digest_ref"] == "ctx://digest/s1"
    assert pr_with_session["bundle"]["artifact_refs"]
    assert pr_with_session["bundle"]["completeness_ledger"]["review_comments_loaded"] is True

    assert persisted_calls, "expected persist function to be called when session_id is provided"
    assert all(call["session_id"] == "s1" for call in persisted_calls)
    assert calls, "asset URLs should trigger attachment materialization"
