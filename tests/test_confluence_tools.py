"""Tests for enhanced Confluence tools added in PR #219"""

import pytest
import re
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def mock_confluence_channel():
    """Mock ConfluenceChannel for testing"""
    with patch('src.confluence.api.confluence_channel') as mock:
        mock._request = AsyncMock(return_value='{"result": "ok"}')
        yield mock


@pytest.mark.asyncio
async def test_confluence_delete_page(mock_confluence_channel):
    """Test confluence_delete_page function"""
    from src.confluence.api import confluence_delete_page
    result = await confluence_delete_page("PAGE-123")
    mock_confluence_channel._request.assert_called()
    assert "deleted" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_get_page_history(mock_confluence_channel):
    """Test confluence_get_page_history function"""
    from src.confluence.api import confluence_get_page_history
    mock_confluence_channel._request.return_value = '[{"version": 1}]'
    result = await confluence_get_page_history("PAGE-123")
    mock_confluence_channel._request.assert_called()
    assert "version" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_get_page_children(mock_confluence_channel):
    """Test confluence_get_page_children function"""
    from src.confluence.api import confluence_get_page_children
    mock_confluence_channel._request.return_value = '[{"title": "child"}]'
    result = await confluence_get_page_children("PAGE-123")
    mock_confluence_channel._request.assert_called()
    assert "child" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_get_space(mock_confluence_channel):
    """Test confluence_get_space function"""
    from src.confluence.api import confluence_get_space
    mock_confluence_channel._request.return_value = '{"key": "SPACE"}'
    result = await confluence_get_space("SPACE")
    mock_confluence_channel._request.assert_called()
    assert "SPACE" in result or "Error" in result


@pytest.mark.asyncio
async def test_confluence_list_pages(mock_confluence_channel):
    """Test confluence_list_pages function"""
    from src.confluence.api import confluence_list_pages
    mock_confluence_channel._request.return_value = '[{"title": "page"}]'
    result = await confluence_list_pages("SPACE")
    mock_confluence_channel._request.assert_called()
    assert "page" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_list_pages_with_limit(mock_confluence_channel):
    """Test confluence_list_pages with limit"""
    from src.confluence.api import confluence_list_pages
    mock_confluence_channel._request.return_value = '[]'
    result = await confluence_list_pages("SPACE", limit=50)
    mock_confluence_channel._request.assert_called()
    assert result is not None


@pytest.mark.asyncio
async def test_confluence_get_user(mock_confluence_channel):
    """Test confluence_get_user function"""
    from src.confluence.api import confluence_get_user
    mock_confluence_channel._request.return_value = '{"displayName": "user"}'
    result = await confluence_get_user("user@example.com")
    mock_confluence_channel._request.assert_called()
    assert "user" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_watch_page(mock_confluence_channel):
    """Test confluence_watch_page function"""
    from src.confluence.api import confluence_watch_page
    result = await confluence_watch_page("PAGE-123")
    mock_confluence_channel._request.assert_called()
    assert "watch" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_unwatch_page(mock_confluence_channel):
    """Test confluence_unwatch_page function"""
    from src.confluence.api import confluence_unwatch_page
    result = await confluence_unwatch_page("PAGE-123")
    mock_confluence_channel._request.assert_called()
    assert "watch" in result.lower() or "Error" in result


@pytest.mark.asyncio
async def test_confluence_search_by_title(mock_confluence_channel):
    """Test confluence_search_by_title function"""
    from src.confluence.api import confluence_search_by_title
    mock_confluence_channel._request.return_value = '[{"title": "result"}]'
    result = await confluence_search_by_title("Search Term")
    mock_confluence_channel._request.assert_called()
    assert "result" in result.lower() or "Error" in result


def test_confluence_get_page_schema_does_not_expose_max_chars():
    from src.confluence import get_tools_schemas

    schemas = get_tools_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "confluence_get_page")
    assert "max_chars" not in schema["function"]["parameters"]["properties"]


def test_confluence_get_page_by_url_schema_does_not_expose_max_chars():
    from src.confluence import get_tools_schemas

    schemas = get_tools_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "confluence_get_page_by_url")
    assert "max_chars" not in schema["function"]["parameters"]["properties"]


def test_confluence_prepare_page_context_schema_exists_without_max_chars():
    from src.confluence import get_tools_schemas

    schemas = get_tools_schemas()
    schema = next(s for s in schemas if s["function"]["name"] == "confluence_prepare_page_context")
    assert "max_chars" not in schema["function"]["parameters"]["properties"]
    assert schema["function"]["parameters"]["properties"]["include_children"]["default"] is True


def test_confluence_preview_tools_not_model_facing():
    from src.confluence import get_tools_schemas

    names = {s.get("function", {}).get("name") for s in get_tools_schemas()}
    assert "confluence_get_page_preview" not in names
    assert "confluence_get_page_by_url_preview" not in names


@pytest.mark.asyncio
async def test_confluence_prepare_page_context_persists_manifest(monkeypatch):
    from src.confluence import confluence_prepare_page_context

    class _Channel:
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_page(self, page_id):
            return {"id": page_id, "title": "Page", "space": {"key": "DOC"}, "body": {"storage": {"value": "<p>hello</p>"}}}
        async def get_all_comments_with_ledger(self, page_id, limit=100):
            return ([{"id": str(i)} for i in range(150)], {"loaded": 150, "total": 150, "complete": True})
        async def get_all_attachments_with_ledger(self, page_id, limit=100):
            return ([{"id": "a1", "title": "a.txt"}], {"loaded": 1, "total": 1, "complete": True})
        async def get_all_page_children_with_ledger(self, page_id, limit=100):
            return ([{"id": "c1", "title": "child"}], {"loaded": 1, "total": 1, "complete": True})

    monkeypatch.setattr("src.confluence.confluence_channel", _Channel())
    out = await confluence_prepare_page_context("123", include_children=True, _session_id="s-conf-prepare")
    assert "[confluence source bundle prepared]" in out
    assert "source_complete: False" in out
    assert "comments_loaded: 150/150" in out
    assert "descendants_not_supported" in out
    assert "source_tree_complete: False" in out


@pytest.mark.asyncio
async def test_confluence_prepare_page_context_marks_partial_when_pagination_incomplete(monkeypatch):
    from src.confluence import confluence_prepare_page_context

    class _Channel:
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_page(self, page_id): return {"id": page_id, "title": "Page", "body": {"storage": {"value": "<p>x</p>"}}}
        async def get_all_comments_with_ledger(self, page_id, limit=100):
            return ([{"id": "1"}], {"loaded": 1, "total": 2, "complete": False})
        async def get_all_attachments_with_ledger(self, page_id, limit=100):
            return ([], {"loaded": 0, "total": 0, "complete": True})
        async def get_all_page_children_with_ledger(self, page_id, limit=100):
            return ([], {"loaded": 0, "total": 0, "complete": True})

    monkeypatch.setattr("src.confluence.confluence_channel", _Channel())
    out = await confluence_prepare_page_context("123", _session_id="s-conf-partial")
    assert "source_complete: False" in out
    assert "descendants_supported" in out
    assert "source_complete_for_generation" in out


@pytest.mark.asyncio
async def test_confluence_get_page_default_requests_children(monkeypatch):
    from src.confluence import confluence_get_page

    captured = {}

    async def _fake_prepare(*, page_id_or_url, include_comments=True, include_attachments=True, include_children=True, include_raw_snapshot=True, _session_id=None):
        captured["include_children"] = include_children
        captured["session_id"] = _session_id
        return "[confluence source bundle prepared]\ncontext_ref: ctx://context/s1/k/abc"

    monkeypatch.setattr("src.confluence.confluence_prepare_page_context", _fake_prepare)
    out = await confluence_get_page("123", _session_id="s1")
    assert "[confluence source bundle prepared]" in out
    assert captured["include_children"] is True
    assert captured["session_id"] == "s1"


@pytest.mark.asyncio
async def test_execute_tool_confluence_get_page_by_url_uses_session_scoped_context_ref(monkeypatch):
    from src import execute_tool
    from src.context_tools import context_read_ref

    class _Channel:
        def is_configured(self): return True
        def get_instance_client(self, **kwargs): return self
        async def get_page(self, page_id):
            return {"id": page_id, "title": "Page", "space": {"key": "DOC"}, "body": {"storage": {"value": "<p>hello</p>"}}}
        async def get_all_comments_with_ledger(self, page_id, limit=100):
            return ([], {"loaded": 0, "total": 0, "complete": True})
        async def get_all_attachments_with_ledger(self, page_id, limit=100):
            return ([], {"loaded": 0, "total": 0, "complete": True})
        async def get_all_page_children_with_ledger(self, page_id, limit=100):
            return ([], {"loaded": 0, "total": 0, "complete": True})

    monkeypatch.setattr("src.confluence.confluence_channel", _Channel())
    result = await execute_tool("confluence_get_page_by_url", url="https://wiki.local/pages/123/Title", _session_id="s1")
    assert result.success is True
    assert "ctx://context/s1/" in result.content
    assert "ctx://context/unknown_session/" not in result.content
    assert "children_loaded:" in result.content
    assert "descendants_supported:" in result.content
    ref = re.search(r"context_ref:\s*(ctx://context/[^\s\"\\]+)", result.content).group(1)
    read_back = await context_read_ref(ref=ref, _session_id="s1")
    assert "source bundle" in read_back.lower() or "metadata" in read_back.lower()


@pytest.mark.asyncio
async def test_execute_tool_confluence_prepare_context_defaults_include_children(monkeypatch):
    from src import execute_tool

    captured = {}

    async def _fake_prepare(*, page_id_or_url, include_comments=True, include_attachments=True, include_children=True, include_raw_snapshot=True, _session_id=None):
        captured["include_children"] = include_children
        return "ok"

    monkeypatch.setattr("src.confluence.confluence_prepare_page_context", _fake_prepare)
    result = await execute_tool("confluence_prepare_page_context", page_id_or_url="123", _session_id="s2")
    assert result.success is True
    assert captured["include_children"] is True


@pytest.mark.asyncio
async def test_confluence_get_comments_is_ledger_aware_and_bounded(monkeypatch):
    from src.confluence import confluence_get_comments
    from src.context_blob_store import read_ref

    class _Channel:
        def is_configured(self): return True
        async def get_all_comments_with_ledger(self, page_id, limit=100):
            comments = [{"id": str(i), "body": {"storage": {"value": "x" * 400}}} for i in range(120)]
            return comments, {"loaded": 120, "total": 120, "complete": True}

    monkeypatch.setattr("src.confluence.confluence_channel", _Channel())
    out = await confluence_get_comments("555", _session_id="s-com")
    assert "[confluence comments prepared]" in out
    assert "comments_loaded: 120/120" in out
    assert "comments_complete: True" in out
    ref = re.search(r"context_ref:\s*(ctx://context/[^\s\"\\]+)", out).group(1)
    raw = read_ref(ref, session_id="s-com", section="raw", max_chars=12000)
    assert "\"comments\"" in raw


@pytest.mark.asyncio
async def test_confluence_get_page_children_is_ledger_aware(monkeypatch):
    from src.confluence import confluence_get_page_children

    class _Channel:
        def is_configured(self): return True
        async def get_all_page_children_with_ledger(self, page_id, limit=100):
            return [{"id": "c1", "title": "Child 1"}], {"loaded": 1, "total": 1, "complete": True}

    monkeypatch.setattr("src.confluence.confluence_channel", _Channel())
    out = await confluence_get_page_children("42", limit=10)
    assert "[confluence children prepared]" in out
    assert "children_complete: True" in out
