"""Confluence Integration - Single source of truth for Confluence operations."""

import logging
from typing import Optional

from src.utils.attachment import download_and_process_attachment

from .api import (
    ConfluenceChannel, 
    confluence_channel,
)
from .adapter import ConfluenceFormatAdapter, _extract_page_id_from_url

logger = logging.getLogger(__name__)

__all__ = [
    "ConfluenceChannel", 
    "confluence_channel",
    "ConfluenceFormatAdapter",
    "confluence_get_page",
    "confluence_search",
    "confluence_get_page_by_url",
    "confluence_create_page",
    "confluence_update_page",
    "confluence_get_comments",
    "confluence_add_comment",
    "confluence_list_spaces",
    "confluence_delete_page",
    "confluence_get_page_history",
    "confluence_get_page_children",
    "confluence_get_space",
    "confluence_list_pages",
    "confluence_get_user",
    "confluence_watch_page",
    "confluence_unwatch_page",
    "confluence_search_by_title",
]


# Factory for creating adapter instances bound to the current channel.


def _get_adapter() -> ConfluenceFormatAdapter:
    """Create a new format adapter bound to the current channel.
    
    A new adapter is returned on each call to avoid caching a channel instance
    in a module-level singleton, which can interfere with tests or runtime reconfiguration.
    """
    return ConfluenceFormatAdapter(confluence_channel)


# ========== Tool Functions ==========


def _infer_attachment_media_type(att: dict, filename: str) -> str:
    """Infer attachment media type from metadata and filename."""
    media_type = att.get("metadata", {}).get("mediaType")
    if media_type:
        return media_type

    media_type = att.get("extensions", {}).get("mediaType")
    if media_type:
        return media_type

    extension_to_media_type = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
        "svg": "image/svg+xml",
        "txt": "text/plain",
        "md": "text/markdown",
        "json": "application/json",
        "csv": "text/csv",
        "pdf": "application/pdf",
    }

    if "." in filename:
        extension = filename.rsplit(".", 1)[-1].lower()
        return extension_to_media_type.get(extension, "application/octet-stream")

    return "application/octet-stream"


def _is_image_attachment(att: dict, filename: str) -> bool:
    """Check whether an attachment is an image."""
    media_type = _infer_attachment_media_type(att, filename)
    return media_type.startswith("image/")


async def _process_confluence_attachments(
    page_id: str,
    channel: Optional[ConfluenceChannel] = None,
) -> str:
    """Process page attachments and return for LLM."""
    channel = channel or confluence_channel
    try:
        attachments = await channel.get_attachments(page_id)
    except Exception as e:
        logger.debug(f"No attachments for page {page_id}: {e}")
        return ""

    if not attachments:
        return ""

    logger.info(f"Processing {len(attachments)} attachments for page {page_id}")

    shown_attachments = attachments[:5]
    omitted_count = max(len(attachments) - len(shown_attachments), 0)

    results = []
    for att in shown_attachments:
        filename = att.get("title", "unknown")
        size = att.get("extensions", {}).get("fileSize", 0)
        link = att.get("_links", {}).get("download", "")
        media_type = _infer_attachment_media_type(att, filename)

        if _is_image_attachment(att, filename):
            if size:
                results.append(
                    f"- **{filename}** (image, {size} bytes) [image attachment not auto-expanded]"
                )
            else:
                results.append(f"- **{filename}** (image) [image attachment not auto-expanded]")
            continue

        if link:
            base_url = channel.base_url.rstrip("/")
            auth_header = channel._auth_header if channel.is_configured() else None
            download_url = f"{base_url}{link}"

            try:
                result = await download_and_process_attachment(
                    url=download_url,
                    session_id=f"confluence-{page_id}",
                    options={
                        "include_image_data": True,
                        "prefer_text_for_images": True,
                        "vision_enabled": False,
                    },
                    auth_header=auth_header,
                )

                if result.content_format == "text" and result.content:
                    preview = result.content[:500]
                    if size:
                        results.append(f"- **{filename}** ({media_type}, {size} bytes)")
                    else:
                        results.append(f"- **{filename}** ({media_type})")
                    results.append(f"  {preview}")
                elif result.content_format == "base64":
                    if size:
                        results.append(
                            f"- **{filename}** ({media_type}, {size} bytes) [binary attachment omitted]"
                        )
                    else:
                        results.append(f"- **{filename}** ({media_type}) [binary attachment omitted]")
                else:
                    if size:
                        results.append(f"- **{filename}** ({media_type}, {size} bytes)")
                    else:
                        results.append(f"- **{filename}** ({media_type})")
            except Exception as e:
                logger.warning(f"Failed to process {filename}: {e}")
                if size:
                    results.append(f"- **{filename}** ({media_type}, {size} bytes) - [processing failed]")
                else:
                    results.append(f"- **{filename}** ({media_type}) - [processing failed]")
        else:
            if size:
                results.append(f"- **{filename}** ({media_type}, {size} bytes)")
            else:
                results.append(f"- **{filename}** ({media_type})")

    if results:
        header = f"**Attachments:** (showing first {len(shown_attachments)} of {len(attachments)})"
        if omitted_count > 0:
            results.append(f"- ... and {omitted_count} more attachment(s) omitted")
        return header + "\n" + "\n".join(results) + "\n"

    return ""


async def _render_page_with_attachments(
    page_id: str,
    *,
    channel: ConfluenceChannel,
    format: str = "markdown",
    max_chars: Optional[int] = None,
) -> str:
    """Render page content plus processed attachments using a specific channel."""
    adapter = ConfluenceFormatAdapter(channel)
    page_content = await adapter.get_page(page_id, format=format, max_chars=max_chars)
    attachment_info = await _process_confluence_attachments(page_id, channel=channel)
    return page_content + ("\n" + attachment_info if attachment_info else "")

async def confluence_get_page(
    page_id: str,
    format: str = "markdown",
    max_chars: Optional[int] = None
) -> str:
    """Get a Confluence page by ID.
    
    Args:
        page_id: Page ID
        format: "markdown" (default) or "storage"
        max_chars: Optional explicit response shortening. Leave None for full Confluence page content.
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        return await _render_page_with_attachments(
            page_id,
            channel=confluence_channel,
            format=format,
            max_chars=max_chars,
        )
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages.
    
    Returns title + url + excerpt (plain text).
    
    Args:
        query: CQL search query
        max_results: Maximum results (default 10)
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.search(query, limit=max_results)
    except Exception as e:
        return f"Error searching: {e}"


async def confluence_get_page_by_url(
    url: str,
    format: str = "markdown",
    max_chars: Optional[int] = None
) -> str:
    """Get a Confluence page by its URL.
    
    Uses the URL to find the correct Confluence instance automatically.
    
    Args:
        url: Full Confluence page URL
        format: "markdown" (default) or "storage"
        max_chars: Optional explicit response shortening. Leave None for full Confluence page content.
    """
    try:
        page_id = _extract_page_id_from_url(url)
        if not page_id:
            return f"Could not extract page ID from URL: {url}"

        instance_channel = confluence_channel.get_instance_client(url=url, strict=True)
        if instance_channel is None:
            return f"Confluence instance for URL is not configured: {url}"

        if not instance_channel.is_configured():
            return f"Confluence instance for URL is not configured: {url}"

        return await _render_page_with_attachments(
            page_id,
            channel=instance_channel,
            format=format,
            max_chars=max_chars,
        )
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_create_page(
    space_key: str,
    title: str,
    body: str = "",
    parent_id: str = None,
    body_format: str = "markdown"
) -> str:
    """Create a new Confluence page.
    
    Args:
        space_key: Space key (e.g., 'DEV')
        title: Page title
        body: Page content
        parent_id: Parent page ID (optional)
        body_format: "markdown" (default) or "storage"
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.create_page(space_key, title, body, body_format=body_format, parent_id=parent_id)
    except Exception as e:
        return f"Error creating page: {e}"


async def confluence_update_page(
    page_id: str,
    title: str = None,
    body: str = None,
    body_format: str = "markdown"
) -> str:
    """Update an existing Confluence page.
    
    Args:
        page_id: Page ID
        title: New title (optional, fetches current if not provided)
        body: New content (optional)
        body_format: "markdown" (default) or "storage"
    """
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        adapter = _get_adapter()
        return await adapter.update_page(page_id, title=title, body=body, body_format=body_format)
    except Exception as e:
        return f"Error updating page: {e}"


async def confluence_get_comments(page_id: str) -> str:
    """Get all comments on a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_comments(page_id)
        
        if isinstance(result, dict):
            comments = result.get("results", [])
            if not comments:
                return "No comments found."
            
            lines = [f"**Comments** ({len(comments)}):\n"]
            for c in comments:
                if not isinstance(c, dict):
                    continue
                body = c.get("body", {})
                if isinstance(body, dict):
                    body = body.get("storage", {}).get("value", "")
                lines.append(f"- {body}")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error getting comments: {e}"


async def confluence_add_comment(page_id: str, comment: str) -> str:
    """Add a comment to a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.add_comment(page_id, comment)
        return f"Comment added to page {page_id}"
    except Exception as e:
        return f"Error adding comment: {e}"


async def confluence_list_spaces(limit: int = 20) -> str:
    """List all Confluence spaces."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.list_spaces(limit)
        
        if isinstance(result, dict):
            spaces = result.get("results", [])
            if not spaces:
                return "No spaces found."
            
            lines = [f"**Spaces** ({len(spaces)}):\n"]
            for s in spaces:
                if not isinstance(s, dict):
                    continue
                name = s.get("name", "Untitled")
                key = s.get("key", "?")
                lines.append(f"- **{name}** ({key})")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error listing spaces: {e}"


async def confluence_delete_page(page_id: str) -> str:
    """Delete a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.delete_page(page_id)
        return f"Page {page_id} deleted successfully"
    except Exception as e:
        return f"Error deleting page: {e}"


async def confluence_get_page_history(page_id: str) -> str:
    """Get version history of a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_page_history(page_id)
        
        if isinstance(result, dict):
            versions = result.get("results", [])
            if not versions:
                return "No history found."
            
            lines = [f"**Version History** ({len(versions)} versions):\n"]
            for v in versions:
                if not isinstance(v, dict):
                    continue
                number = v.get("number", "?")
                when = v.get("createdAt", "")[:10]
                author = v.get("author", {}).get("displayName", "Unknown") if isinstance(v.get("author"), dict) else "Unknown"
                lines.append(f"- v{number} by {author} on {when}")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error getting page history: {e}"


async def confluence_get_page_children(page_id: str, limit: int = 10) -> str:
    """Get child pages of a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_page_children(page_id, limit)
        
        if isinstance(result, list):
            if not result:
                return "No child pages found."
            
            lines = [f"**Child Pages** ({len(result)}):\n"]
            for p in result:
                if not isinstance(p, dict):
                    continue
                title = p.get("title", "Untitled")
                child_id = p.get("id", "?")
                lines.append(f"- **{title}** ({child_id})")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error getting page children: {e}"


async def confluence_get_space(space_key: str) -> str:
    """Get details of a Confluence space."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_space(space_key)
        
        if isinstance(result, dict):
            name = result.get("name", "Untitled")
            description = result.get("description", {}).get("plain", {}).get("value", "No description")
            return f"**Space: {name}** ({space_key})\n\n{description}"
        return str(result)
    except Exception as e:
        return f"Error getting space: {e}"


async def confluence_list_pages(space_key: str, limit: int = 20) -> str:
    """List all pages in a Confluence space."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.list_pages(space_key, limit)
        
        if isinstance(result, dict):
            pages = result.get("results", [])
            if not pages:
                return "No pages found in this space."
            
            lines = [f"**Pages in {space_key}** ({len(pages)}):\n"]
            for p in pages:
                if not isinstance(p, dict):
                    continue
                title = p.get("title", "Untitled")
                page_id = p.get("id", "?")
                lines.append(f"- **{title}** ({page_id})")
            return "\n".join(lines)
        return str(result)
    except Exception as e:
        return f"Error listing pages: {e}"


async def confluence_get_user(user_id: str = None, username: str = None) -> str:
    """Get user details from Confluence."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_user(user_id, username)
        
        if isinstance(result, dict):
            display_name = result.get("displayName", "Unknown")
            email = result.get("publicName", "No email")
            return f"**User: {display_name}**\nEmail: {email}"
        return str(result)
    except Exception as e:
        return f"Error getting user: {e}"


async def confluence_watch_page(page_id: str) -> str:
    """Watch a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.watch_page(page_id)
        return f"Now watching page {page_id}"
    except Exception as e:
        return f"Error watching page: {e}"


async def confluence_unwatch_page(page_id: str) -> str:
    """Unwatch a Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        await confluence_channel.unwatch_page(page_id)
        return f"Stopped watching page {page_id}"
    except Exception as e:
        return f"Error unwatching page: {e}"


async def confluence_search_by_title(title: str, space_key: str = None) -> str:
    """Search Confluence pages by title."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.get_page_by_title(space_key, title)
        
        if result and isinstance(result, dict):
            page_id = result.get("id", "?")
            page_title = result.get("title", title)
            url = result.get("url", "")
            return f"**{page_title}**\nID: {page_id}\nURL: {url}"
        return f"No page found with title: {title}"
    except Exception as e:
        return f"Error searching by title: {e}"


def get_tools_schemas() -> list:
    """Return Confluence tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page",
                "description": "Get a Confluence page by its ID. Returns Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Confluence page ID (numeric)"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Output format: markdown (default) or storage"
                        },
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_by_url",
                "description": "Get a Confluence page directly by its full URL. Returns Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full Confluence page URL"},
                        "format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Output format: markdown (default) or storage"
                        },
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Search Confluence pages using CQL. Returns title + url + excerpt.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "CQL search query"},
                        "max_results": {"type": "integer", "description": "Maximum results to return", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search_by_title",
                "description": "Search Confluence pages by exact title",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Page title to search for"},
                        "space_key": {"type": "string", "description": "Optional space key to limit search", }
                    },
                    "required": ["title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_create_page",
                "description": "Create a new Confluence page. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key (e.g., 'DEV', 'TEAM')"},
                        "title": {"type": "string", "description": "Page title"},
                        "body": {"type": "string", "description": "Page content (Markdown or HTML)", "default": ""},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Input format: markdown (default) or storage"
                        },
                        "parent_id": {"type": "string", "description": "Parent page ID for hierarchy", }
                    },
                    "required": ["space_key", "title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_update_page",
                "description": "Update an existing Confluence page. Accepts Markdown by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to update"},
                        "title": {"type": "string", "description": "New title (optional)"},
                        "body": {"type": "string", "description": "New content (Markdown or HTML, optional)"},
                        "body_format": {
                            "type": "string",
                            "enum": ["markdown", "storage"],
                            "default": "markdown",
                            "description": "Input format: markdown (default) or storage"
                        }
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_delete_page",
                "description": "Delete a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to delete"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_comments",
                "description": "Get all comments on a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_add_comment",
                "description": "Add a comment to a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"},
                        "comment": {"type": "string", "description": "Comment text"}
                    },
                    "required": ["page_id", "comment"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_list_spaces",
                "description": "List all Confluence spaces",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Maximum number of spaces to return", "default": 20}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_space",
                "description": "Get details of a Confluence space",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key (e.g., 'DEV', 'TEAM')"}
                    },
                    "required": ["space_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_list_pages",
                "description": "List all pages in a Confluence space",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key"},
                        "limit": {"type": "integer", "description": "Maximum number of pages", "default": 20}
                    },
                    "required": ["space_key"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_children",
                "description": "Get child pages of a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Parent page ID"},
                        "limit": {"type": "integer", "description": "Maximum number of children", "default": 10}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_history",
                "description": "Get version history of a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_user",
                "description": "Get user details from Confluence",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string", "description": "User ID", },
                        "username": {"type": "string", "description": "Username", }
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_watch_page",
                "description": "Watch a Confluence page (get notifications)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to watch"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_unwatch_page",
                "description": "Stop watching a Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to unwatch"}
                    },
                    "required": ["page_id"]
                }
            }
        },
    ]
