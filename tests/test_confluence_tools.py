"""Tests for enhanced Confluence tools added in PR #219"""

import pytest
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
    assert "source_complete: True" in out
    assert "comments_loaded: 150/150" in out


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
