"""
Confluence Format Adapter - Unified interface for Markdown/Storage formats.

Provides:
- Query: get_page, search (default: Markdown)
- Write: create_page, update_page (default: Markdown)
"""

import logging
import re
from typing import Any, Dict, List, Optional

from ..utils.truncate import truncate
from .api import ConfluenceChannel
from .converter import converter

logger = logging.getLogger(__name__)


def _extract_page_id_from_url(url: str) -> Optional[str]:
    """Extract Confluence page ID from URL."""
    # Format 1: /pages/ID/title
    match = re.search(r"/pages/(\d+)/", url)
    if match:
        return match.group(1)

    # Format 2: ?pageId=ID
    match = re.search(r"[?&]pageId=(\d+)", url)
    if match:
        return match.group(1)

    # Format 3: /pages/ID (no title)
    match = re.search(r"/pages/(\d+)(?:\?|$)", url)
    if match:
        return match.group(1)

    return None


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
            max_chars: Optional explicit response shortening. Leave None for full Confluence page content.
            
        Returns:
            Page content in requested format
        """
        page = await self.channel.get_page(page_id)
        
        if not isinstance(page, dict):
            return f"Error: Invalid page response"
        
        if format == "storage":
            # Storage format: return raw body content only
            content = self._extract_storage(page)
        else:
            # Markdown format: include title as header
            content = await self._to_markdown(page)
        
        # Apply character limit
        content = truncate(content, max_chars) if max_chars else content
        
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
            max_chars: Optional explicit response shortening. Leave None for full Confluence page content.
            
        Returns:
            Page content in requested format
        """
        # Extract page ID from URL
        page_id = _extract_page_id_from_url(url)
        if not page_id:
            return f"Could not extract page ID from URL: {url}"
        
        # Get instance-specific channel and fetch page
        instance_channel = self.channel.get_instance_client(url=url, strict=True)
        if instance_channel is None:
            return f"Confluence instance for URL is not configured: {url}"
        if not instance_channel.is_configured():
            return f"Confluence instance for URL is not configured: {url}"

        page = await instance_channel.get_page(page_id)
        
        if not isinstance(page, dict):
            return f"Error: Invalid page response"
        
        if format == "storage":
            content = self._extract_storage(page)
        else:
            content = await self._to_markdown(page)
        
        content = truncate(content, max_chars) if max_chars else content
        
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
        import re
        
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
            
            # Get URL - try _links.webui first, then fall back to top-level url
            links = p.get("_links", {})
            url = links.get("webui", "") or p.get("url", "")
            if url and hasattr(self.channel, 'base_url'):
                base = self.channel.base_url
                if base and not url.startswith('http'):
                    url = base.rstrip('/') + url
            
            # Derive excerpt from body if available
            excerpt = ""
            body = p.get("body", {})
            if isinstance(body, dict):
                storage = body.get("storage", {})
                if isinstance(storage, dict):
                    content = storage.get("value", "")
                    # Strip HTML tags and truncate
                    excerpt = re.sub(r'<[^>]+>', '', content)
                    excerpt = excerpt.strip()[:200] + "..." if len(excerpt) > 200 else excerpt
            
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
        # Use channel's _request directly since list_pages may not exist
        try:
            result = await self.channel._request(
                "GET",
                f"/space/{space_key}/content",
                params={"limit": limit, "type": "page"}
            )
        except AttributeError:
            # Fallback if _request not available
            return "Error: Unable to list pages (method not available)"
        
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
            content=body,
            parent_id=parent_id
        )
        
        if isinstance(result, dict):
            page_id = result.get("id", "")
            url = result.get("_links", {}).get("webui", "")
            # Prefix with base_url to create a full URL
            if url and hasattr(self.channel, 'base_url'):
                base = self.channel.base_url
                if base and not url.startswith('http'):
                    url = base.rstrip('/') + url
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
            title: New title (optional, fetches current if not provided)
            body: New content (optional)
            body_format: "markdown" (default) or "storage"
            
        Returns:
            Success message
        """
        # Track if body was provided by caller (not fetched)
        body_provided = body is not None
        current_version = None
        
        # Fetch current page if we need title or body
        current_page = None
        if title is None or body is None:
            current_page = await self.channel.get_page(page_id)
            if isinstance(current_page, dict):
                if title is None:
                    title = current_page.get("title", "")
                if body is None:
                    body_obj = current_page.get("body", {})
                    if isinstance(body_obj, dict):
                        body = body_obj.get("storage", {}).get("value", "")
                    elif isinstance(body_obj, str):
                        body = body_obj
                    else:
                        body = ""
                # Extract version to avoid extra API call
                version_info = current_page.get("version", {})
                if isinstance(version_info, dict):
                    current_version = version_info.get("number")
            else:
                # Failed to fetch current page - return error if we still need title/body
                if title is None or body is None:
                    return f"Error: Could not fetch current page {page_id} to get missing title/body"
        
        # Only convert if caller provided body AND wants markdown conversion
        # If we fetched the current content, keep it as storage
        if body and body_format == "markdown" and body_provided:
            body = self.converter.markdown_to_storage(body)
        
        result = await self.channel.update_page(
            page_id=page_id,
            title=title,
            content=body,
            current_version=current_version
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
