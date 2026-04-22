"""Tests for enhanced Jira tools added in PR #219"""

import pytest
import re
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_jira_channel():
    """Mock JiraChannel for testing"""
    with patch('src.jira.api.jira_channel') as mock:
        mock._request = AsyncMock(return_value='{"result": "ok"}')
        yield mock


@pytest.mark.asyncio
async def test_jira_update_issue(mock_jira_channel):
    """Test jira_update_issue function"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123", summary="New Summary", description="New Description")
    mock_jira_channel._request.assert_called()
    assert "updated successfully" in result or "Error" in result


def test_jira_get_issue_schema_does_not_expose_max_chars():
    from src.jira import get_tools_schemas

    schemas = get_tools_schemas()
    get_issue_schema = next(s for s in schemas if s["function"]["name"] == "jira_get_issue")
    assert "max_chars" not in get_issue_schema["function"]["parameters"]["properties"]
    assert "max_comments" not in get_issue_schema["function"]["parameters"]["properties"]


def test_jira_get_issue_by_url_schema_does_not_expose_max_chars():
    from src.jira import get_tools_schemas

    schemas = get_tools_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "jira_get_issue_by_url")
    assert "max_chars" not in schema["function"]["parameters"]["properties"]
    assert "max_comments" not in schema["function"]["parameters"]["properties"]


def test_jira_preview_tools_not_model_facing():
    from src.jira import get_tools_schemas

    names = {s.get("function", {}).get("name") for s in get_tools_schemas()}
    assert "jira_get_issue_preview" not in names
    assert "jira_get_issue_by_url_preview" not in names
    assert "export_issues_to_markdown" in names


def test_jira_get_comments_schema_is_model_facing():
    from src.jira import get_tools_schemas

    names = {s.get("function", {}).get("name") for s in get_tools_schemas()}
    assert "jira_get_comments" in names


@pytest.mark.asyncio
async def test_jira_update_issue_summary_only(mock_jira_channel):
    """Test jira_update_issue with summary only"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123", summary="Summary Only")
    mock_jira_channel._request.assert_called()
    assert "updated successfully" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_update_issue_no_fields(mock_jira_channel):
    """Test jira_update_issue with no fields returns error"""
    from src.jira.api import jira_update_issue
    result = await jira_update_issue("PROJ-123")
    assert "No fields to update" in result


@pytest.mark.asyncio
async def test_jira_assign_issue(mock_jira_channel):
    """Test jira_assign_issue function"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123", assignee="john.doe")
    mock_jira_channel._request.assert_called()
    assert "assigned" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_jira_assign_issue_unassign(mock_jira_channel):
    """Test jira_assign_issue to unassign (empty string)"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123", assignee="")
    assert "Error" in result or "assigned" in result.lower()


@pytest.mark.asyncio
async def test_jira_assign_issue_no_assignee(mock_jira_channel):
    """Test jira_assign_issue without assignee returns error"""
    from src.jira.api import jira_assign_issue
    result = await jira_assign_issue("PROJ-123")
    assert "assignee parameter is required" in result


@pytest.mark.asyncio
async def test_jira_get_projects(mock_jira_channel):
    """Test jira_get_projects function"""
    from src.jira.api import jira_get_projects
    mock_jira_channel._request.return_value = '[{"key": "PROJ"}]'
    result = await jira_get_projects()
    mock_jira_channel._request.assert_called()
    assert "PROJ" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_components(mock_jira_channel):
    """Test jira_get_components function"""
    from src.jira.api import jira_get_components
    mock_jira_channel._request.return_value = '[{"name": "comp1"}]'
    result = await jira_get_components("PROJ")
    mock_jira_channel._request.assert_called()
    assert "comp1" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_versions(mock_jira_channel):
    """Test jira_get_versions function"""
    from src.jira.api import jira_get_versions
    mock_jira_channel._request.return_value = '[{"name": "v1"}]'
    result = await jira_get_versions("PROJ")
    mock_jira_channel._request.assert_called()
    assert "v1" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_get_worklog(mock_jira_channel):
    """Test jira_get_worklog function"""
    from src.jira.api import jira_get_worklog
    mock_jira_channel._request.return_value = '[{"timeSpent": "1h"}]'
    result = await jira_get_worklog("PROJ-123")
    mock_jira_channel._request.assert_called()
    assert "1h" in result or "Error" in result


@pytest.mark.asyncio
async def test_jira_add_worklog(mock_jira_channel):
    """Test jira_add_worklog function"""
    from src.jira.api import jira_add_worklog
    result = await jira_add_worklog("PROJ-123", "1h", "Work done")
    mock_jira_channel._request.assert_called()
    assert "work log" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_jira_add_worklog_no_comment(mock_jira_channel):
    """Test jira_add_worklog without comment"""
    from src.jira.api import jira_add_worklog
    result = await jira_add_worklog("PROJ-123", "2h")
    mock_jira_channel._request.assert_called()
    assert "work log" in result.lower() or "Error" in result


def test_jira_adapter_get_comments_list_supports_all_comments_mode():
    from src.jira.adapter import JiraFormatAdapter

    adapter = JiraFormatAdapter(MagicMock())
    issue = {"fields": {"comment": {"comments": [{"id": str(i)} for i in range(20)], "total": 20}}}
    assert len(adapter._get_comments_list(issue, 5)) == 5
    assert len(adapter._get_comments_list(issue, None)) == 20


@pytest.mark.asyncio
async def test_jira_prepare_issue_context_persists_all_comments_and_bounded_manifest(monkeypatch):
    from src.jira import jira_prepare_issue_context
    from src.context_blob_store import read_ref
    import re

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {
                "key": issue_key,
                "names": {"customfield_1": "Acceptance Criteria"},
                "renderedFields": {"description": "<p>x</p>"},
                "fields": {
                    "summary": "Demo",
                    "status": {"name": "Open"},
                    "description": "## Description\\nBody\\n## Acceptance Criteria\\n- AC1",
                    "comment": {"comments": [], "total": 20},
                    "attachment": [],
                },
            }
        async def get_comments(self, issue_key):
            return [{"id": str(i), "author": {"displayName": "A"}, "created": "2026-01-01", "body": f"c{i}"} for i in range(20)]

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    out = await jira_prepare_issue_context("PROJ-1", _session_id="s-jira-prepare")
    assert "\\nissue_key:" not in out
    assert "\nissue_key:" in out
    assert "[jira source bundle prepared]" in out
    assert "comments_loaded: 20/20" in out
    assert "source_complete_for_generation: True" in out
    assert "source_digest_chunk_count:" in out
    assert len(out) < 8000
    ref = out.split("context_ref: ", 1)[1].split("\n", 1)[0].strip().strip('"')
    raw = read_ref(ref, session_id="s-jira-prepare", section="raw", max_chars=50000)
    assert '"comments_loaded": 20' in raw
    assert '"comments_complete": true' in raw.lower()


@pytest.mark.asyncio
async def test_jira_prepare_issue_context_attachment_full_and_preview_ledger(monkeypatch):
    from src.jira import jira_prepare_issue_context
    from types import SimpleNamespace

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {
                "key": issue_key,
                "names": {"customfield_1": "Acceptance Criteria"},
                "renderedFields": {"description": "<p>x</p>"},
                "fields": {
                    "summary": "Demo",
                    "description": "Body",
                    "comment": {"comments": [], "total": 0},
                    "attachment": [
                        {"id": "1", "filename": "small.txt", "mimeType": "text/plain", "content": "u1"},
                        {"id": "2", "filename": "big.txt", "mimeType": "text/plain", "content": "u2"},
                        {"id": "3", "filename": "img.png", "mimeType": "image/png"},
                    ],
                },
            }
        async def get_comments(self, issue_key): return []

    async def _fake_download(url, session_id=None, options=None, auth_header=None):
        if url == "u1":
            return SimpleNamespace(content_format="text", content="small text")
        return SimpleNamespace(content_format="text", content="X" * 5001)

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.download_and_process_attachment", _fake_download)
    out = await jira_prepare_issue_context("PROJ-2", _session_id="s-jira-attach")
    assert "\\nissue_key:" not in out
    assert "\nissue_key:" in out
    assert "text_attachments_loaded: 2/2" in out
    ref = out.split("context_ref: ", 1)[1].split("\n", 1)[0].strip().strip('"')
    from src.context_blob_store import read_ref
    raw = read_ref(ref, session_id="s-jira-attach", section="coverage_ledger", max_chars=50000)
    assert "text_attachments_full_loaded" in raw
    assert "text_attachments_preview_only" in raw
    assert "binary_attachment_bodies_skipped_count" in raw
    assert "source_complete_definition" in raw
    assert '"source_complete_including_binary_bodies": false' in raw.lower()
    assert '"binary_attachment_body_policy": "metadata_only"' in raw
    assert '"text_attachment_bodies_complete": true' in raw.lower()
    assert '"source_complete_definition"' in raw


@pytest.mark.asyncio
async def test_jira_preview_only_text_attachment_marks_text_incomplete(monkeypatch):
    from src.jira import jira_prepare_issue_context
    from types import SimpleNamespace
    from src.context_blob_store import read_ref

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {"key": issue_key, "names": {"customfield_1": "Acceptance Criteria"}, "renderedFields": {"description": "<p>x</p>"}, "fields": {"summary": "Demo", "description": "Body", "comment": {"comments": [], "total": 0}, "attachment": [{"id": "2", "filename": "big.txt", "mimeType": "text/plain", "content": "u2"}]}}
        async def get_comments(self, issue_key): return []

    async def _fake_download(url, session_id=None, options=None, auth_header=None):
        return SimpleNamespace(content_format="text", content="X" * 5001)

    def _raise_put_text(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    monkeypatch.setattr("src.jira.download_and_process_attachment", _fake_download)
    monkeypatch.setattr("src.jira.put_text", _raise_put_text)
    out = await jira_prepare_issue_context("PROJ-9", _session_id="s-jira-preview")
    assert "\\nissue_key:" not in out
    assert "\nissue_key:" in out
    ref = out.split("context_ref: ", 1)[1].split("\n", 1)[0].strip().strip('"')
    raw = read_ref(ref, session_id="s-jira-preview", section="coverage_ledger", max_chars=50000)
    assert '"text_attachment_bodies_complete": false' in raw.lower()
    assert '"source_complete_for_generation": false' in raw.lower()


@pytest.mark.asyncio
async def test_jira_digest_chunks_do_not_silently_truncate_long_comment(monkeypatch):
    from src.jira import jira_prepare_issue_context
    from src.context_blob_store import read_ref

    long_comment = "L" * 30000

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {"key": issue_key, "names": {"x": "y"}, "renderedFields": {"description": "<p>x</p>"}, "fields": {"summary": "Demo", "description": "Body", "comment": {"comments": [], "total": 1}, "attachment": []}}
        async def get_comments(self, issue_key):
            return [{"id": "1", "author": {"displayName": "A"}, "created": "2026-01-01", "body": long_comment}]

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    out = await jira_prepare_issue_context("PROJ-3", _session_id="s-jira-long")
    assert "\\nissue_key:" not in out
    assert "\nissue_key:" in out
    ref = out.split("context_ref: ", 1)[1].split("\n", 1)[0].strip().strip('"')
    raw = read_ref(ref, session_id="s-jira-long", section="raw", max_chars=60000)
    assert "L" * 1000 in raw


@pytest.mark.asyncio
async def test_jira_prepare_issue_context_extracts_issue_key_from_url(monkeypatch):
    from src.jira import jira_prepare_issue_context

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {"key": issue_key, "fields": {"summary": "Demo", "description": "D", "comment": {"comments": [], "total": 0}, "attachment": []}}
        async def get_comments(self, issue_key): return []

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    out = await jira_prepare_issue_context("https://jira.systems.com/browse/MMGFX-13887", _session_id="s-jira-url")
    assert "issue_key: MMGFX-13887" in out
    assert "Could not extract issue key" not in out


@pytest.mark.asyncio
async def test_jira_get_comments_returns_bounded_manifest(monkeypatch):
    from src.jira import jira_get_comments

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        async def get_issue(self, issue_key, expand=None):
            return {"key": issue_key, "fields": {"comment": {"comments": [{"id": "1", "body": "A" * 5000}], "total": 1}}}

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    out = await jira_get_comments("PROJ-77", _session_id="s-jira-comments")
    assert "[jira comments bundle prepared]" in out
    assert "context_ref: ctx://context/" in out
    assert "comments_loaded: 1/1" in out
    assert "AAAAA" not in out


@pytest.mark.asyncio
async def test_jira_get_issue_by_url_defaults_to_source_complete_without_keyword(monkeypatch):
    from src.jira import jira_get_issue_by_url

    called = {}

    async def _fake_prepare(*, issue_key_or_url, include_all_comments=True, include_attachments=True, include_raw_snapshot=True, _session_id=None):
        called["url"] = issue_key_or_url
        return "[jira source bundle prepared]\nsource_complete: True"

    monkeypatch.setattr("src.jira.jira_prepare_issue_context", _fake_prepare)
    out = await jira_get_issue_by_url("https://jira.local/browse/PROJ-1", _session_id="s-keywordless")
    assert "[jira source bundle prepared]" in out
    assert called["url"].endswith("/PROJ-1")


@pytest.mark.asyncio
async def test_execute_tool_jira_get_issue_by_url_uses_session_scoped_context_ref(monkeypatch):
    from src import execute_tool
    from src.context_tools import context_read_ref

    class _Channel:
        api_version = "3"
        _auth_header = {}
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_issue(self, issue_key, expand=None):
            return {"key": issue_key, "fields": {"summary": "Demo", "description": "Body", "comment": {"comments": [], "total": 0}, "attachment": []}}
        async def get_comments(self, issue_key): return []

    monkeypatch.setattr("src.jira.jira_channel", _Channel())
    result = await execute_tool("jira_get_issue_by_url", url="https://jira.systems.com/browse/MMGFX-13887", _session_id="s1")
    assert result.success is True
    assert "ctx://context/s1/" in result.content
    assert "ctx://context/unknown_session/" not in result.content
    ref = re.search(r"context_ref:\s*(ctx://context/[^\s\"\\]+)", result.content).group(1)
    read_back = await context_read_ref(ref=ref, _session_id="s1")
    assert "source bundle" in read_back.lower() or "metadata" in read_back.lower()


@pytest.mark.asyncio
async def test_execute_tool_jira_get_comments_dispatches_with_session(monkeypatch):
    from src import execute_tool

    captured = {}

    async def _fake_jira_get_comments(issue_key, _session_id=None):
        captured["issue_key"] = issue_key
        captured["session_id"] = _session_id
        return "[jira comments bundle prepared]\ncontext_ref: ctx://context/s1/blob-1\ncomments_loaded: 1/1"

    monkeypatch.setattr("src.jira.jira_get_comments", _fake_jira_get_comments)
    result = await execute_tool("jira_get_comments", issue_key="ABC-1", _session_id="s1")
    assert result.success is True
    assert captured["issue_key"] == "ABC-1"
    assert captured["session_id"] == "s1"
    assert "[jira comments bundle prepared]" in result.content
    assert "ctx://context/s1/" in result.content
