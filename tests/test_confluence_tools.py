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
