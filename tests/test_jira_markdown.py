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
    async def test_get_issue_markdown_attachments_hide_urls_by_default(self, mock_channel):
        """Attachment section should list filenames only by default."""
        from src.jira.adapter import JiraFormatAdapter

        mock_channel.get_issue = AsyncMock(return_value={
            "key": "PROJ-123",
            "fields": {
                "summary": "Test Issue",
                "status": {"name": "Open"},
                "description": "Description text",
                "attachment": [
                    {"filename": "design.png", "content": "https://jira.example.com/very/long/url/design.png"}
                ],
                "comment": {"comments": []},
            },
        })

        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123", format="markdown")

        assert "- design.png" in result
        assert "https://jira.example.com/very/long/url/design.png" not in result

    @pytest.mark.asyncio
    async def test_get_issue_markdown_attachments_can_include_urls(self, mock_channel):
        """Optional flag keeps previous URL-inclusive attachment format."""
        from src.jira.adapter import JiraFormatAdapter

        mock_channel.get_issue = AsyncMock(return_value={
            "key": "PROJ-123",
            "fields": {
                "summary": "Test Issue",
                "status": {"name": "Open"},
                "description": "Description text",
                "attachment": [
                    {"filename": "design.png", "content": "https://jira.example.com/very/long/url/design.png"}
                ],
                "comment": {"comments": []},
            },
        })

        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.get_issue("PROJ-123", format="markdown", include_attachment_urls=True)

        assert "- design.png (https://jira.example.com/very/long/url/design.png)" in result
    
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
        call_args = mock_channel.create_issue.call_args
        desc = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("description", "")
        
        # For Server/DC (api_version="2"), description should be converted
        # from Markdown "# Description" to Jira wiki "h1. Description"
        assert isinstance(desc, str), f"Expected str, got {type(desc)}"
        assert "h1." in desc, f"Expected wiki header, got: {desc}"
    
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
        call_args = mock_channel.create_issue.call_args
        desc = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs.get("description", "")
        
        # For Cloud (api_version="3"), should be ADF dict
        assert isinstance(desc, dict), f"Expected dict, got {type(desc)}"
    
    @pytest.mark.asyncio
    async def test_add_comment_markdown(self, mock_channel):
        """Test add_comment converts Markdown to wiki."""
        from src.jira.adapter import JiraFormatAdapter
        
        adapter = JiraFormatAdapter(mock_channel)
        result = await adapter.add_comment("PROJ-123", "Comment with **bold**")
        
        mock_channel.add_comment.assert_called_once()
        
        # Verify conversion happened
        call_args = mock_channel.add_comment.call_args
        body = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("comment", "")
        
        # For Server/DC, should be converted to wiki (bold ** → *)
        assert isinstance(body, str), f"Expected str, got {type(body)}"
        assert "*bold*" in body, f"Expected wiki bold, got: {body}"
    
    @pytest.mark.asyncio
    async def test_add_comment_markdown_cloud(self, mock_channel):
        """Test add_comment converts Markdown to ADF for Cloud."""
        from src.jira.adapter import JiraFormatAdapter
        
        mock_channel.api_version = "3"  # Cloud
        adapter = JiraFormatAdapter(mock_channel)
        
        result = await adapter.add_comment("PROJ-123", "Comment with **bold**")
        
        mock_channel.add_comment.assert_called_once()
        
        # Verify ADF dict was created
        call_args = mock_channel.add_comment.call_args
        body = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("comment", "")
        
        # For Cloud, should be ADF dict
        assert isinstance(body, dict), f"Expected dict, got {type(body)}"


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


class TestMarkdownToWikiEdgeCases:
    """Test edge cases for Markdown to wiki conversion."""
    
    def test_bold_not_converted_to_italic(self):
        """Test that **bold** doesn't become italic after conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "This is **bold** text"
        result = markdown_to_wiki(md)
        
        # Should be *bold* (Jira bold), not _bold_ (Jira italic)
        assert "*bold*" in result
        assert "_bold_" not in result
    
    def test_italic_with_asterisks(self):
        """Test that *italic* converts correctly."""
        from src.jira.converter import markdown_to_wiki
        
        md = "This is *italic* text"
        result = markdown_to_wiki(md)
        
        # Single asterisk should become underscore (Jira italic)
        assert "_italic_" in result
    
    def test_mixed_bold_and_italic(self):
        """Test mixed bold and italic in same line."""
        from src.jira.converter import markdown_to_wiki
        
        md = "**bold** and *italic*"
        result = markdown_to_wiki(md)
        
        assert "*bold*" in result
        assert "_italic_" in result
    
    def test_list_with_formatting(self):
        """Test list items containing bold/code/links."""
        from src.jira.converter import markdown_to_wiki
        
        md = "- Item with **bold**\n- Item with `code`\n- Item with [link](url)"
        result = markdown_to_wiki(md)
        
        assert "* Item with *bold*" in result
        assert "* Item with {{code}}" in result
        assert "* Item with [link|url]" in result
    
    def test_image_before_link(self):
        """Test that images are converted before links."""
        from src.jira.converter import markdown_to_wiki
        
        md = "![alt](img.png) and [link](url)"
        result = markdown_to_wiki(md)
        
        # Image should be !img.png! and link should be [link|url]
        assert "!img.png!" in result
        assert "[link|url]" in result
    
    def test_quote_conversion(self):
        """Test blockquote conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "> Quote text"
        result = markdown_to_wiki(md)
        
        assert "{quote}Quote text{quote}" in result
    
    def test_horizontal_rule(self):
        """Test horizontal rule conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "Text\n\n---\n\nMore text"
        result = markdown_to_wiki(md)
        
        assert "----" in result
    
    def test_ordered_list(self):
        """Test ordered list conversion."""
        from src.jira.converter import markdown_to_wiki
        
        md = "1. First\n2. Second"
        result = markdown_to_wiki(md)
        
        assert "# First" in result
        assert "# Second" in result


class TestURLExtraction:
    """Test URL extraction for jira_get_issue_by_url."""
    
    def test_url_extraction_simple(self):
        """Test simple URL extraction."""
        from src.jira.converter import JiraMarkupConverter
        
        # Just test the regex pattern
        import re
        url = "https://company.atlassian.net/browse/PROJ-123"
        match = re.search(r'/browse/([A-Z][A-Z0-9_]*-\d+)', url, re.IGNORECASE)
        
        assert match is not None
        assert match.group(1).upper() == "PROJ-123"
    
    def test_url_extraction_with_digits(self):
        """Test URL with digits in project key."""
        import re
        url = "https://company.atlassian.net/browse/ABC1-456"
        match = re.search(r'/browse/([A-Z][A-Z0-9_]*-\d+)', url, re.IGNORECASE)
        
        assert match is not None
        assert match.group(1).upper() == "ABC1-456"
    
    def test_url_extraction_lowercase(self):
        """Test URL with lowercase (case insensitive)."""
        import re
        url = "https://company.atlassian.net/browse/proj-789"
        match = re.search(r'/browse/([A-Z][A-Z0-9_]*-\d+)', url, re.IGNORECASE)
        
        assert match is not None
        assert match.group(1).upper() == "PROJ-789"
