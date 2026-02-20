"""
Confluence Channel - Full API support for Confluence wiki.

Features:
- Get, create, update pages
- Page search using CQL
- Space management
- Comment management
"""

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx

from src.config import config

logger = logging.getLogger(__name__)


class ConfluenceChannel:
    """Confluence channel adapter with full REST API support."""
    
    def __init__(self):
        self.base_url = config.confluence.get("url", "").rstrip("/")
        self.username = config.confluence.get("username", "")
        self.api_token = config.confluence.get("api_token", "")
        self.space = config.confluence.get("space", "")
        self.enabled = config.confluence.get("enabled", False)
        
        self.client = httpx.AsyncClient(timeout=30.0)
        self._auth_header = self._get_auth_header()
    
    def _get_auth_header(self) -> Dict[str, str]:
        """Get authorization header."""
        if self.username and self.api_token:
            creds = f"{self.username}:{self.api_token}"
            token = base64.b64encode(creds.encode()).decode()
            return {"Authorization": f"Basic {token}"}
        return {}
    
    def is_configured(self) -> bool:
        """Check if Confluence is properly configured."""
        return bool(self.base_url and self.username and self.api_token and self.enabled)
    
    def reinit(self):
        """Reinitialize ConfluenceChannel (called when config changes)."""
        logger.info("Reinitializing ConfluenceChannel...")
        self.base_url = config.confluence.get("url", "").rstrip("/")
        self.username = config.confluence.get("username", "")
        self.api_token = config.confluence.get("api_token", "")
        self.space = config.confluence.get("space", "")
        self.enabled = config.confluence.get("enabled", False)
        self._auth_header = self._get_auth_header()
        logger.info("ConfluenceChannel reinitialized")
    
    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Confluence API."""
        if not self.is_configured():
            raise RuntimeError("Confluence not configured")
        
        url = f"{self.base_url}/rest/api{endpoint}"
        headers = {
            **self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        response = await self.client.request(
            method, url, json=data, params=params, headers=headers
        )
        response.raise_for_status()
        return response.json() if response.text else {}
    
    # ========== Page Operations ==========
    
    async def get_page(self, page_id: str) -> Dict[str, Any]:
        """Get a page by ID.
        
        Args:
            page_id: Page ID (numeric string)
            
        Returns:
            Page details with content
        """
        logger.info(f"Fetching page: {page_id}")
        return await self._request("GET", f"/content/{page_id}", params={
            "expand": "body.storage,version,space,ancestors"
        })
    
    async def get_page_by_title(
        self,
        space_key: str,
        title: str
    ) -> Optional[Dict[str, Any]]:
        """Find a page by title in a space.
        
        Args:
            space_key: Space key (e.g., "DEV")
            title: Page title
            
        Returns:
            Page details or None if not found
        """
        logger.info(f"Searching for page: {title} in {space_key}")
        
        # Use CQL to find page
        result = await self.search_pages(f'space="{space_key}" AND title="{title}"', limit=1)
        pages = result.get("results", [])
        return pages[0] if pages else None
    
    async def create_page(
        self,
        space_key: str,
        title: str,
        content: str,
        parent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new page.
        
        Args:
            space_key: Space key (e.g., "DEV")
            title: Page title
            content: Page content (supports storage format)
            parent_id: Parent page ID (optional)
            
        Returns:
            Created page details
        """
        logger.info(f"Creating page: {title} in {space_key}")
        
        data = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage"
                }
            }
        }
        
        if parent_id:
            data["ancestors"] = [{"id": parent_id}]
        
        return await self._request("POST", "/content", data=data)
    
    async def update_page(
        self,
        page_id: str,
        title: str,
        content: str,
        current_version: int = None
    ) -> Dict[str, Any]:
        """Update an existing page.
        
        Args:
            page_id: Page ID
            title: New title
            content: New content
            current_version: Current version number (auto-fetched if not provided)
            
        Returns:
            Updated page details
        """
        logger.info(f"Updating page: {page_id}")
        
        # Get current version if not provided
        if current_version is None:
            page = await self.get_page(page_id)
            current_version = page.get("version", {}).get("number", 1)
        
        data = {
            "type": "page",
            "title": title,
            "body": {
                "storage": {
                    "value": content,
                    "representation": "storage"
                }
            },
            "version": {
                "number": current_version + 1,
                "minorEdit": False
            }
        }
        
        return await self._request("PUT", f"/content/{page_id}", data=data)
    
    async def delete_page(self, page_id: str) -> bool:
        """Delete a page.
        
        Args:
            page_id: Page ID to delete
            
        Returns:
            True if successful
        """
        logger.info(f"Deleting page: {page_id}")
        await self._request("DELETE", f"/content/{page_id}")
        return True
    
    # ========== Search ==========
    
    async def search_pages(
        self,
        cql: str,
        limit: int = 25,
        start: int = 0
    ) -> Dict[str, Any]:
        """Search pages using CQL (Confluence Query Language).
        
        Args:
            cql: CQL query string
            limit: Maximum results
            start: Pagination offset
            
        Returns:
            Search results with pages list
        """
        logger.info(f"Searching pages with CQL: {cql[:100]}...")
        
        params = {
            "cql": cql,
            "limit": limit,
            "start": start,
            "expand": "body.storage,version,space"
        }
        
        return await self._request("GET", "/content/search", params=params)
    
    async def search_pages_by_content(
        self,
        query: str,
        space_key: str = None,
        limit: int = 25
    ) -> List[Dict[str, Any]]:
        """Search pages by content text.
        
        Args:
            query: Text to search for
            space_key: Optional space filter
            limit: Maximum results
            
        Returns:
            List of matching pages
        """
        cql = f'text ~ "{query}"'
        if space_key:
            cql += f' AND space = "{space_key}"'
        cql += " ORDER BY lastmodified DESC"
        
        result = await self.search_pages(cql, limit=limit)
        return result.get("results", [])
    
    # ========== Space Operations ==========
    
    async def get_space(self, space_key: str) -> Dict[str, Any]:
        """Get space details.
        
        Args:
            space_key: Space key
            
        Returns:
            Space details
        """
        return await self._request("GET", f"/space/{space_key}", params={
            "expand": "homepage,description.plain,description.view"
        })
    
    async def list_spaces(self, limit: int = 25) -> List[Dict[str, Any]]:
        """List all spaces.
        
        Args:
            limit: Maximum results
            
        Returns:
            List of spaces
        """
        result = await self._request("GET", "/space", params={"limit": limit})
        return result.get("results", [])
    
    # ========== Comment Operations ==========
    
    async def add_comment(self, page_id: str, comment: str) -> Dict[str, Any]:
        """Add a comment to a page.
        
        Args:
            page_id: Page ID
            comment: Comment text
            
        Returns:
            Created comment details
        """
        logger.info(f"Adding comment to page: {page_id}")
        
        data = {
            "type": "comment",
            "body": {
                "storage": {
                    "value": f"<p>{comment}</p>",
                    "representation": "storage"
                }
            }
        }
        
        return await self._request("POST", f"/content/{page_id}/child/comment", data=data)
    
    async def get_comments(self, page_id: str) -> List[Dict[str, Any]]:
        """Get all comments on a page.
        
        Args:
            page_id: Page ID
            
        Returns:
            List of comments
        """
        result = await self._request("GET", f"/content/{page_id}/child/comment")
        return result.get("results", [])
    
    # ========== Labels ==========
    
    async def add_label(self, page_id: str, label: str) -> bool:
        """Add a label to a page.
        
        Args:
            page_id: Page ID
            label: Label name
            
        Returns:
            True if successful
        """
        logger.info(f"Adding label '{label}' to page: {page_id}")
        
        data = {"prefix": "global", "name": label}
        await self._request("POST", f"/content/{page_id}/label", data=[data])
        return True
    
    async def remove_label(self, page_id: str, label: str) -> bool:
        """Remove a label from a page.
        
        Args:
            page_id: Page ID
            label: Label name
            
        Returns:
            True if successful
        """
        await self._request("DELETE", f"/content/{page_id}/label/{label}")
        return True
    
    def reinit(self):
        """Reinitialize ConfluenceChannel (called when config changes)."""
        logger.info("Reinitializing ConfluenceChannel...")
        self.base_url = config.confluence.get("url", "").rstrip("/")
        self.username = config.confluence.get("username", "")
        self.api_token = config.confluence.get("api_token", "")
        self.space = config.confluence.get("space", "")
        self.enabled = config.confluence.get("enabled", False)
        self._auth_header = self._get_auth_header()
        logger.info("ConfluenceChannel reinitialized")
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global channel instance
confluence_channel = ConfluenceChannel()

# Register for config reload
from src.config import service_reload_manager
service_reload_manager.register('confluence', confluence_channel.reinit)


# ========== Tool Functions for Agent ==========

async def confluence_get_page(page_id: str) -> str:
    """Get a Confluence page by ID."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        page = await confluence_channel.get_page(page_id)
        return _format_page_info(page)
    except Exception as e:
        return f"Error getting page {page_id}: {str(e)}"


async def confluence_search(cql: str, limit: int = 10) -> str:
    """Search Confluence pages using CQL."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        result = await confluence_channel.search_pages(cql, limit=limit)
        pages = result.get("results", [])
        total = result.get("size", 0)
        
        if not pages:
            return f"No pages found for CQL: {cql}"
        
        lines = [f"**Search Results** ({total} total, showing {len(pages)}):\n"]
        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id")
            space = page.get("space", {}).get("key", "?")
            lines.append(f"- **{title}** ({space}-{page_id})")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching pages: {str(e)}"


async def confluence_create_page(
    space_key: str,
    title: str,
    content: str,
    parent_id: str = None
) -> str:
    """Create a new Confluence page."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        space = space_key or confluence_channel.space
        result = await confluence_channel.create_page(space, title, content, parent_id)
        page_id = result.get("id", "unknown")
        url = f"{confluence_channel.base_url}/pages/viewpage.action?pageId={page_id}"
        return f"Page created: **{title}**\nID: {page_id}\nURL: {url}"
    except Exception as e:
        return f"Error creating page: {str(e)}"


async def confluence_update_page(
    page_id: str,
    title: str,
    content: str
) -> str:
    """Update a Confluence page."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        result = await confluence_channel.update_page(page_id, title, content)
        page_id = result.get("id", page_id)
        return f"Page updated: **{title}** (ID: {page_id})"
    except Exception as e:
        return f"Error updating page: {str(e)}"


async def confluence_get_comments(page_id: str) -> str:
    """Get comments on a Confluence page."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        comments = await confluence_channel.get_comments(page_id)
        
        if not comments:
            return f"No comments on page {page_id}"
        
        lines = [f"**Comments on page {page_id}:**\n"]
        for comment in comments:
            author = comment.get("by", {}).get("displayName", "Unknown")
            created = comment.get("created", "")[:10]
            body = _extract_comment_body(comment)
            lines.append(f"\n**{author}** ({created}):\n{body[:200]}")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting comments: {str(e)}"


async def confluence_add_comment(page_id: str, comment: str) -> str:
    """Add a comment to a Confluence page."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        result = await confluence_channel.add_comment(page_id, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added to page {page_id}: ID={comment_id}"
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def confluence_list_spaces(limit: int = 20) -> str:
    """List available Confluence spaces."""
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    
    try:
        spaces = await confluence_channel.list_spaces(limit)
        
        if not spaces:
            return "No spaces found"
        
        lines = [f"**Confluence Spaces** ({len(spaces)}):\n"]
        for space in spaces:
            key = space.get("key", "?")
            name = space.get("name", "Unknown")
            lines.append(f"- **{name}** (key: {key})")
        
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing spaces: {str(e)}"


# ========== Utility Functions ==========

def _format_page_info(page: Dict) -> str:
    """Format page information for display."""
    title = page.get("title", "Untitled")
    page_id = page.get("id", "?")
    space = page.get("space", {}).get("key", "?")
    version = page.get("version", {}).get("number", "?")
    last_modified = page.get("version", {}).get("when", "")[:10]
    
    # Get body content
    body = page.get("body", {})
    storage_body = body.get("storage", {})
    content = storage_body.get("value", "")[:1000]
    
    url = f"https://confluence.atlassian.com/pages/viewpage.action?pageId={page_id}"
    
    return f"""**{title}**

**ID:** {page_id}
**Space:** {space}
**Version:** {version}
**Last Modified:** {last_modified}
**URL:** {url}

**Content Preview:**
{content}{'...' if len(storage_body.get('value', '')) > 1000 else ''}"""


def _extract_comment_body(comment: Dict) -> str:
    """Extract text from comment body."""
    body = comment.get("body", {})
    storage = body.get("storage", {})
    return storage.get("value", "").replace("<p>", "").replace("</p>", "").strip()
