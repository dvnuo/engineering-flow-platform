"""
Confluence Format Adapter - Unified interface for Markdown/Storage formats.

Provides:
- Query: get_page, search (default: Markdown)
- Write: create_page, update_page (default: Markdown)
"""

import logging
from typing import Any, Dict, List, Optional

from .api import ConfluenceChannel
from .converter import converter

logger = logging.getLogger(__name__)


class ConfluenceFormatAdapter:
    """
    Unified interface for Confluence operations with format conversion.
    
    - Query operations return Markdown by default
    - Write operations accept Markdown by default
    - Storage format is supported as fallback
    """
    
    def __init__(self, channel: ConfluenceChannel):
        self.channel = channel
        self.converter = converter
    
    # ========== Query Operations ==========
    
    async def get_page(
        self,
        page_id: str,
        format: str = "markdown",
        max_chars: Optional[int] = None
    ) -> str:
        """
        Get a Confluence page by ID.
        
        Args:
            page_id: Page ID
            format: "markdown" (default) or "storage"
            max_chars: Limit response length (avoid token overflow)
            
        Returns:
            Page content in requested format
        """
        page = await self.channel.get_page(page_id)
        
        if not isinstance(page, dict):
            return f"Error: Invalid page response"
        
        if format == "storage":
            content = self._extract_storage(page)
        else:
            # format == "markdown"
            content = await self._to_markdown(page)
        
        # Apply character limit
        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... (truncated, {max_chars} chars limit)"
        
        return content
    
    async def get_page_by_url(
        self,
        url: str,
        format: str = "markdown",
        max_chars: Optional[int] = None
    ) -> str:
        """
        Get a Confluence page by URL.
        
        Args:
            url: Full Confluence page URL
            format: "markdown" (default) or "storage"
            max_chars: Limit response length
            
        Returns:
            Page content in requested format
        """
        # Get instance-specific channel
        instance_channel = self.channel.get_instance_client(url=url)
        page = await instance_channel.get_page_by_url(url)
        
        if not isinstance(page, dict):
            return f"Error: Invalid page response"
        
        if format == "storage":
            content = self._extract_storage(page)
        else:
            content = await self._to_markdown(page)
        
        if max_chars and len(content) > max_chars:
            content = content[:max_chars] + f"\n\n... (truncated, {max_chars} chars limit)"
        
        return content
    
    async def search(
        self,
        query: str,
        limit: int = 10
    ) -> str:
        """
        Search Confluence pages.
        
        Returns title + url + excerpt (plain text), not full content.
        User can call get_page for full content if needed.
        
        Args:
            query: CQL search query
            limit: Maximum results
            
        Returns:
            Formatted search results
        """
        result = await self.channel.search_pages(query, limit)
        
        if not isinstance(result, dict):
            return "Error: Invalid search response"
        
        pages = result.get("results", [])
        
        if not pages:
            return "No pages found."
        
        lines = [f"**Search Results** ({len(pages)}):\n"]
        
        for p in pages:
            if not isinstance(p, dict):
                continue
            
            title = p.get("title", "Untitled")
            url = p.get("url", "")
            excerpt = p.get("excerpt", "")
            
            # Clean excerpt (remove HTML tags)
            import re
            excerpt = re.sub(r'<[^>]+>', '', excerpt)
            excerpt = excerpt[:200] + "..." if len(excerpt) > 200 else excerpt
            
            lines.append(f"- **{title}**")
            lines.append(f"  {url}")
            if excerpt:
                lines.append(f"  _{excerpt}_")
            lines.append("")
        
        return "\n".join(lines)
    
    async def list_pages(
        self,
        space_key: str,
        limit: int = 25
    ) -> str:
        """
        List pages in a space.
        
        Returns page titles in Markdown format.
        
        Args:
            space_key: Space key
            limit: Maximum results
            
        Returns:
            List of pages
        """
        result = await self.channel.list_pages(space_key, limit)
        
        if not isinstance(result, dict):
            return "Error: Invalid response"
        
        pages = result.get("results", [])
        
        if not pages:
            return f"No pages found in space {space_key}."
        
        lines = [f"**Pages in {space_key}** ({len(pages)}):\n"]
        
        for p in pages:
            if not isinstance(p, dict):
                continue
            
            title = p.get("title", "Untitled")
            page_id = p.get("id", "")
            lines.append(f"- [{title}](#{page_id})")
        
        return "\n".join(lines)
    
    # ========== Write Operations ==========
    
    async def create_page(
        self,
        space_key: str,
        title: str,
        body: str = "",
        body_format: str = "markdown",
        parent_id: Optional[str] = None
    ) -> str:
        """
        Create a new Confluence page.
        
        Args:
            space_key: Space key (e.g., 'DEV')
            title: Page title
            body: Page content
            body_format: "markdown" (default) or "storage"
            parent_id: Parent page ID (optional)
            
        Returns:
            Success message with page URL
        """
        if body_format == "markdown" and body:
            body = self.converter.markdown_to_storage(body)
        
        result = await self.channel.create_page(
            space_key=space_key,
            title=title,
            body=body,
            parent_id=parent_id
        )
        
        if isinstance(result, dict):
            page_id = result.get("id", "")
            url = result.get("_links", {}).get("webui", "")
            return f"Page created successfully: {title}\nID: {page_id}\nURL: {url}"
        
        return str(result)
    
    async def update_page(
        self,
        page_id: str,
        title: Optional[str] = None,
        body: Optional[str] = None,
        body_format: str = "markdown"
    ) -> str:
        """
        Update an existing Confluence page.
        
        Args:
            page_id: Page ID
            title: New title (optional)
            body: New content (optional)
            body_format: "markdown" (default) or "storage"
            
        Returns:
            Success message
        """
        if body and body_format == "markdown":
            body = self.converter.markdown_to_storage(body)
        
        result = await self.channel.update_page(
            page_id=page_id,
            title=title,
            body=body
        )
        
        if result:
            return f"Page {page_id} updated successfully"
        
        return f"Error updating page {page_id}"
    
    # ========== Internal Methods ==========
    
    async def _to_markdown(self, page: Dict[str, Any]) -> str:
        """Convert page to Markdown format."""
        title = page.get("title", "Untitled")
        
        # Extract body content
        body_obj = page.get("body", {})
        if isinstance(body_obj, dict):
            storage_value = body_obj.get("storage", {}).get("value", "")
        elif isinstance(body_obj, str):
            storage_value = body_obj
        else:
            storage_value = ""
        
        if not storage_value:
            return f"# {title}\n\n_No content_"
        
        # Convert to markdown
        try:
            markdown_content = self.converter.storage_to_markdown(storage_value)
        except Exception as e:
            logger.error(f"Conversion to markdown failed: {e}")
            markdown_content = storage_value
        
        return f"# {title}\n\n{markdown_content}"
    
    def _extract_storage(self, page: Dict[str, Any]) -> str:
        """Extract storage format from page."""
        body_obj = page.get("body", {})
        if isinstance(body_obj, dict):
            return body_obj.get("storage", {}).get("value", "")
        elif isinstance(body_obj, str):
            return body_obj
        return ""
