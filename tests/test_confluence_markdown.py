"""Tests for Confluence Markdown support (feature/confluence-markdown-support)"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestConverter:
    """Test Markdown ↔ Storage converter."""
    
    def test_markdown_to_storage_basic(self):
        """Test basic Markdown to Storage conversion."""
        from src.confluence.converter import markdown_to_storage
        
        md = "# Hello\n\n**bold**"
        result = markdown_to_storage(md)
        
        assert "Hello" in result
    
    def test_storage_to_markdown_basic(self):
        """Test basic Storage to Markdown conversion."""
        from src.confluence.converter import storage_to_markdown
        
        storage = "<h1>Hello</h1><p><strong>bold</strong></p>"
        result = storage_to_markdown(storage)
        
        assert "Hello" in result
        assert "**bold**" in result
    
    def test_markdown_to_storage_code_block(self):
        """Test code block conversion."""
        from src.confluence.converter import markdown_to_storage
        
        md = "```python\nprint('hello')\n```"
        result = markdown_to_storage(md)
        
        assert "code-block" in result
    
    def test_storage_to_markdown_code_block(self):
        """Test code block reverse conversion."""
        from src.confluence.converter import storage_to_markdown
        
        storage = '<ac:code-block lang="python">print("hello")</ac:code-block>'
        result = storage_to_markdown(storage)
        
        assert "```" in result
    
    def test_markdown_to_storage_link(self):
        """Test link conversion."""
        from src.confluence.converter import markdown_to_storage
        
        md = "[Google](https://google.com)"
        result = markdown_to_storage(md)
        
        assert "href" in result
    
    def test_storage_to_markdown_link(self):
        """Test link reverse conversion."""
        from src.confluence.converter import storage_to_markdown
        
        storage = '<a href="https://google.com">Google</a>'
        result = storage_to_markdown(storage)
        
        assert "Google" in result
    
    def test_roundtrip_simple(self):
        """Test roundtrip: MD -> Storage -> MD"""
        from src.confluence.converter import markdown_to_storage, storage_to_markdown
        
        original = "# Title\n\nHello **world**"
        storage = markdown_to_storage(original)
        restored = storage_to_markdown(storage)
        
        assert "Title" in restored
        assert "Hello" in restored

    def test_storage_to_markdown_multiline_attachment_image(self):
        """Regression: multiline attachment images should not be dropped."""
        from src.confluence.converter import storage_to_markdown

        storage = '<ac:image>\n  <ri:attachment ri:filename="img.png"/>\n</ac:image>'
        result = storage_to_markdown(storage)

        assert result.strip()
        assert "attachment:img.png" in result

    def test_storage_to_markdown_multiline_url_image(self):
        """Regression: multiline URL images should not be dropped."""
        from src.confluence.converter import storage_to_markdown

        storage = '<ac:image>\n  <ri:url ri:value="https://example.com/a.png"/>\n</ac:image>'
        result = storage_to_markdown(storage)

        assert result.strip()
        assert "https://example.com/a.png" in result

    def test_storage_to_markdown_absolute_attachment_image(self):
        """Regression: non-self-closing attachment images should not be dropped."""
        from src.confluence.converter import storage_to_markdown

        storage = (
            '<ac:image><ri:attachment ri:filename="img.png">'
            '<ri:page ri:content-title="Other Page"/></ri:attachment></ac:image>'
        )
        result = storage_to_markdown(storage)

        assert result.strip()
        assert "attachment:img.png" in result


class TestAdapter:
    """Test ConfluenceFormatAdapter."""
    
    @pytest.fixture
    def mock_channel(self):
        """Create mock ConfluenceChannel."""
        channel = MagicMock()
        channel.get_page = AsyncMock(return_value={
            "title": "Test Page",
            "body": {"storage": {"value": "<h1>Hello</h1>"}}
        })
        channel.create_page = AsyncMock(return_value={"id": "123", "_links": {"webui": "/pages/123"}})
        channel.update_page = AsyncMock(return_value=True)
        channel.search_pages = AsyncMock(return_value={
            "results": [
                {
                    "title": "Page 1",
                    "url": "/pages/1",
                    "_links": {"webui": "/spaces/TEST/pages/Page1"},
                    "body": {"storage": {"value": "<p>This is test content</p>"}}
                }
            ]
        })
        channel.list_pages = AsyncMock(return_value={
            "results": [{"title": "Page 1", "id": "1"}]
        })
        channel.get_instance_client = MagicMock(return_value=channel)
        channel.base_url = ""
        return channel
    
    @pytest.mark.asyncio
    async def test_get_page_markdown(self, mock_channel):
        """Test get_page returns Markdown by default."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        result = await adapter.get_page("123")
        
        assert "Test Page" in result
        assert "<h1>" not in result
    
    @pytest.mark.asyncio
    async def test_get_page_storage(self, mock_channel):
        """Test get_page returns Storage when specified."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        result = await adapter.get_page("123", format="storage")
        
        assert "<h1>" in result
    
    @pytest.mark.asyncio
    async def test_get_page_max_chars(self, mock_channel):
        """Test get_page respects max_chars."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        mock_channel.get_page = AsyncMock(return_value={
            "title": "Test",
            "body": {"storage": {"value": "x" * 1000}}
        })
        
        result = await adapter.get_page("123", max_chars=100)
        
        # truncate() uses "..." suffix
        assert len(result) <= 150
        assert "..." in result
    
    @pytest.mark.asyncio
    async def test_create_page_markdown(self, mock_channel):
        """Test create_page accepts Markdown by default."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        await adapter.create_page("SPACE", "New Page", "# Hello **world**")
        
        mock_channel.create_page.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_page_storage(self, mock_channel):
        """Test create_page accepts Storage when specified."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        await adapter.create_page("SPACE", "New Page", "<h1>Hello</h1>", body_format="storage")
        
        # Verify channel was called with content parameter
        mock_channel.create_page.assert_called_once()
        call_args = mock_channel.create_page.call_args
        # Check for content parameter (not body)
        args = call_args.args if call_args.args else ()
        kwargs = call_args.kwargs if call_args.kwargs else {}
        content = kwargs.get("content")
        assert content is not None
        assert "<h1>" in content
    
    @pytest.mark.asyncio
    async def test_update_page_markdown(self, mock_channel):
        """Test update_page accepts Markdown by default."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        await adapter.update_page("123", body="# Updated")
        
        mock_channel.update_page.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_search_returns_excerpt(self, mock_channel):
        """Test search returns title + url + excerpt."""
        from src.confluence.adapter import ConfluenceFormatAdapter
        
        adapter = ConfluenceFormatAdapter(mock_channel)
        result = await adapter.search("test")
        
        assert "Page 1" in result
        assert "/pages/1" in result or "/spaces/TEST/pages/Page1" in result


class TestToolSchemas:
    """Test tool schemas have correct parameters."""
    
    def test_get_page_schema_has_format(self):
        """Test confluence_get_page schema includes format parameter."""
        from src.confluence import get_tools_schemas
        
        schemas = get_tools_schemas()
        get_page_schema = next(s for s in schemas if s["function"]["name"] == "confluence_get_page")
        
        props = get_page_schema["function"]["parameters"]["properties"]
        
        assert "format" in props
        assert props["format"]["enum"] == ["markdown", "storage"]
        assert props["format"]["default"] == "markdown"
    
    def test_get_page_schema_has_max_chars(self):
        """Test confluence_get_page schema includes max_chars parameter."""
        from src.confluence import get_tools_schemas
        
        schemas = get_tools_schemas()
        get_page_schema = next(s for s in schemas if s["function"]["name"] == "confluence_get_page")
        
        props = get_page_schema["function"]["parameters"]["properties"]
        
        assert "max_chars" in props
        assert "Leave unset for full Confluence page content" in props["max_chars"]["description"]

    def test_confluence_get_page_by_url_schema_max_chars_description_prefers_unset_default(self):
        from src.confluence import get_tools_schemas

        schemas = get_tools_schemas()
        schema = next(s for s in schemas if s["function"]["name"] == "confluence_get_page_by_url")
        desc = schema["function"]["parameters"]["properties"]["max_chars"]["description"]

        assert "Leave unset for full Confluence page content" in desc
        assert "do not set unless the user explicitly asks" in desc
    
    def test_create_page_schema_has_body_format(self):
        """Test confluence_create_page schema includes body_format parameter."""
        from src.confluence import get_tools_schemas
        
        schemas = get_tools_schemas()
        create_schema = next(s for s in schemas if s["function"]["name"] == "confluence_create_page")
        
        props = create_schema["function"]["parameters"]["properties"]
        
        assert "body_format" in props
        assert props["body_format"]["default"] == "markdown"
    
    def test_update_page_schema_has_body_format(self):
        """Test confluence_update_page schema includes body_format parameter."""
        from src.confluence import get_tools_schemas
        
        schemas = get_tools_schemas()
        update_schema = next(s for s in schemas if s["function"]["name"] == "confluence_update_page")
        
        props = update_schema["function"]["parameters"]["properties"]
        
        assert "body_format" in props
        assert props["body_format"]["default"] == "markdown"


class TestBackwardCompatibility:
    """Test backward compatibility with storage format."""
    
    @pytest.mark.asyncio
    async def test_get_page_storage_works(self):
        """Test that storage format parameter works."""
        # Need to patch the module where confluence_channel is imported
        with patch('src.confluence.confluence_channel') as mock_ch:
            mock_ch.is_configured = MagicMock(return_value=True)
            mock_ch.get_page = AsyncMock(return_value={
                "title": "Test",
                "body": {"storage": {"value": "<h1>Hello</h1>"}}
            })
            
            from src.confluence import confluence_get_page
            
            result = await confluence_get_page("123", format="storage")
            # Should not error - verify it contains expected content
            assert "Error" not in result
            assert "<h1>Hello</h1>" in result or "Hello" in result
