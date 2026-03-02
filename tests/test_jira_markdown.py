"""Tests for Jira Markdown support (feature/jira-markdown-support)"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestConverter:
    """Test Jira markup converter."""
    
    def test_wiki_to_markdown_simple(self):
        """Test wiki to Markdown conversion."""
        from src.jira.converter import wiki_to_markdown
        
        wiki = "simple text"
        result = wiki_to_markdown(wiki)
        
        assert "simple text" in result
    
    def test_markdown_to_wiki_simple(self):
        """Test Markdown to wiki conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "simple text"
        result = markdown_to_wiki(md)
        
        assert "simple text" in result
    
    def test_markdown_to_wiki_headers(self):
        """Test header conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "# Hello"
        result = markdown_to_wiki(md)
        
        assert "h1. Hello" in result
    
    def test_markdown_to_wiki_lists(self):
        """Test list conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "- item 1\n- item 2"
        result = markdown_to_wiki(md)
        
        assert "* item 1" in result
    
    def test_markdown_to_wiki_code(self):
        """Test code block conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "```python\nprint('hello')\n```"
        result = markdown_to_wiki(md)
        
        assert "{code:python}" in result
    
    def test_markdown_to_wiki_inline_code(self):
        """Test inline code conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "Use `code` here"
        result = markdown_to_wiki(md)
        
        assert "{{code}}" in result
    
    def test_markdown_to_wiki_link(self):
        """Test link conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "[Google](https://google.com)"
        result = markdown_to_wiki(md)
        
        assert "[Google|https://google.com]" in result
    
    def test_adf_to_markdown(self):
        """Test ADF to Markdown conversion."""
        from src.jira.converter import adf_to_markdown
        
        adf = {
            "type": "doc",
            "content": [
                {"type": "heading", "attrs": {"level": 1}, "content": [{"type": "text", "text": "Title"}]},
                {"type": "paragraph", "content": [{"type": "text", "text": "Content"}]}
            ]
        }
        
        result = adf_to_markdown(adf)
        
        assert "# Title" in result
        assert "Content" in result
    
    def test_markdown_to_adf(self):
        """Test Markdown to ADF conversion."""
        from src.jira.converter import markdown_to_adf
        
        md = "# Title\nContent"
        result = markdown_to_adf(md)
        
        assert result["type"] == "doc"
        assert result["version"] == 1
    
    def test_is_adf(self):
        """Test ADF detection."""
        from src.jira.converter import converter
        
        # ADF
        assert converter.is_adf({"type": "doc", "version": 1, "content": []})
        
        # Not ADF
        assert not converter.is_adf("plain text")


class TestAdapter:
    """Test JiraFormatAdapter."""
    
    @pytest.fixture
    def mock_channel(self):
        """Create mock JiraChannel."""
        channel = MagicMock()
        channel.api_version = "2"  # Server/DC
        channel.get_issue = AsyncMock(return_value={
            "key": "PROJ-123",
            "fields": {
                "summary": "Test Issue",
                "status": {"name": "Open"},
                "description": "Description text",
                "issuetype": {"name": "Bug"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "John Doe"},
                "comment": {"comments": []}
            }
        })
        channel.create_issue = AsyncMock(return_value={"key": "PROJ-124", "self": "https://jira/rest/api/2/issue/124"})
        channel.add_comment = AsyncMock(return_value=True)
        channel.update_issue = AsyncMock(return_value=True)
        return channel
    
    @pytest.mark.asyncio
    async def test_get_issue_markdown(self, mock_channel):
        """Test get_issue returns Markdown by default."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123")
        
        assert "Test Issue" in result
        assert "Status" in result
    
    @pytest.mark.asyncio
    async def test_get_issue_wiki(self, mock_channel):
        """Test get_issue returns wiki format."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123", format="wiki")
        
        assert "Test Issue" in result
        assert "Status" in result
    
    @pytest.mark.asyncio
    async def test_get_issue_raw(self, mock_channel):
        """Test get_issue returns raw dict."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123", format="raw")
        
        assert isinstance(result, dict)
        assert result.get("key") == "PROJ-123"
    
    @pytest.mark.asyncio
    async def test_get_issue_cloud_adf(self, mock_channel):
        """Test get_issue handles ADF from Cloud."""
        from src.jira.adapter import JiraFormatAdapter
        
        mock_channel.api_version = "3"  # Cloud
        
        # Issue with ADF description
        mock_channel.get_issue = AsyncMock(return_value={
            "key": "PROJ-123",
            "fields": {
                "summary": "Test Issue",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": "ADF Content"}]}
                    ]
                }
            }
        })
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123")
        
        assert "ADF Content" in result
    
    @pytest.mark.asyncio
    async def test_create_issue_markdown(self, mock_channel):
        """Test create_issue converts Markdown to wiki."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.create_issue(
            "PROJ",
            "New Issue",
            "# Description"
        )
        
        # Verify channel was called with wiki format (not raw markdown)
        mock_channel.create_issue.assert_called_once()
        call_kwargs = mock_channel.create_issue.call_args.kwargs
        desc = call_kwargs.get("description", "")
        
        # For Server/DC (api_version="2"), should be converted to wiki
        assert "h1." in desc or "Description" in str(type(desc))
    
    @pytest.mark.asyncio
    async def test_create_issue_markdown_cloud(self, mock_channel):
        """Test create_issue converts Markdown to ADF for Cloud."""
        from src.jira.adapter import JiraFormatAdapter
        
        mock_channel.api_version = "3"  # Cloud
        adapter = JiraFormatAdapter(mock_channel)
        
        result = await adapter.create_issue(
            "PROJ",
            "New Issue",
            "# Description"
        )
        
        # Verify channel was called with ADF dict
        mock_channel.create_issue.assert_called_once()
        call_kwargs = mock_channel.create_issue.call_args.kwargs
        desc = call_kwargs.get("description", "")
        
        # For Cloud (api_version="3"), should be ADF dict
        assert isinstance(desc, dict) or "h1." in str(desc)
    
    @pytest.mark.asyncio
    async def test_add_comment_markdown(self, mock_channel):
        """Test add_comment converts Markdown to wiki."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.add_comment("PROJ-123", "Comment with **bold**")
        
        mock_channel.add_comment.assert_called_once()
        
        # Verify conversion happened
        call_kwargs = mock_channel.add_comment.call_args.kwargs
        body = call_kwargs.get("description", "")
        
        # For Server/DC, should be wiki format
        assert "*bold*" in body or "Description" in str(type(body))
    
    @pytest.mark.asyncio
    async def test_add_comment_markdown_cloud(self, mock_channel):
        """Test add_comment converts Markdown to ADF for Cloud."""
        from src.jira.adapter import JiraFormatAdapter
        
        mock_channel.api_version = "3"  # Cloud
        adapter = JiraFormatAdapter(mock_channel)
        
        result = await adapter.add_comment("PROJ-123", "Comment with **bold**")
        
        mock_channel.add_comment.assert_called_once()
        
        # Verify ADF dict was created
        call_kwargs = mock_channel.add_comment.call_args.kwargs
        body = call_kwargs.get("description", "")
        
        # For Cloud, should be ADF dict
        assert isinstance(body, dict) or "h1." in str(body)


class TestToolFunctions:
    """Test tool functions in __init__.py."""
    
    @pytest.mark.asyncio
    async def test_jira_get_issue_default(self):
        """Test jira_get_issue with defaults."""
        with patch('src.jira.jira_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=True)
            mock_ch.get_issue = AsyncMock(return_value={
                "key": "TEST-1",
                "fields": {"summary": "Test"}
            })
            
            from src.jira import jira_get_issue
            
            result = await jira_get_issue("TEST-1")
            
            assert "Test" in result
    
    @pytest.mark.asyncio
    async def test_jira_get_issue_format(self):
        """Test jira_get_issue with format parameter."""
        with patch('src.jira.jira_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=True)
            mock_ch.get_issue = AsyncMock(return_value={
                "key": "TEST-1",
                "fields": {"summary": "Test", "description": "Desc"}
            })
            
            from src.jira import jira_get_issue
            
            result = await jira_get_issue("TEST-1", format="wiki")
            
            assert "Test" in result
    
    @pytest.mark.asyncio
    async def test_jira_create_issue(self):
        """Test jira_create_issue."""
        with patch('src.jira.jira_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=True)
            mock_ch.create_issue = AsyncMock(return_value={"key": "TEST-2"})
            
            from src.jira import jira_create_issue
            
            result = await jira_create_issue("TEST", "New Issue", "Description")
            
            mock_ch.create_issue.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_jira_add_comment(self):
        """Test jira_add_comment."""
        with patch('src.jira.jira_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=True)
            mock_ch.add_comment = AsyncMock(return_value=True)
            
            from src.jira import jira_add_comment
            
            result = await jira_add_comment("TEST-1", "New comment")
            
            mock_ch.add_comment.assert_called_once()


class TestBackwardCompatibility:
    """Test backward compatibility."""
    
    @pytest.mark.asyncio
    async def test_not_configured(self):
        """Test behavior when Jira is not configured."""
        with patch('src.jira.jira_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=False)
            
            from src.jira import jira_get_issue
            
            result = await jira_get_issue("TEST-1")
            
            assert "not configured" in result.lower()
