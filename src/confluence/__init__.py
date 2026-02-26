"""Confluence Integration - Single source of truth for Confluence operations."""

import logging

from .api import (
    ConfluenceChannel, 
    confluence_channel,
)

__all__ = [
    "ConfluenceChannel", 
    "confluence_channel",
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


# ========== Tool Functions ==========

async def confluence_get_page(page_id: str) -> str:
    """Get a Confluence page by ID."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        # Use channel method to get raw page dict
        page = await confluence_channel.get_page(page_id)
        
        # Handle case where page might not be a dict
        if not isinstance(page, dict):
            return f"Error: Invalid page response - expected dict, got {type(page)}"
        
        title = page.get('title', 'Untitled')
        
        # Handle body - could be string or dict
        body_obj = page.get('body', {})
        if isinstance(body_obj, dict):
            body = body_obj.get('storage', {}).get('value', 'No content')
        elif isinstance(body_obj, str):
            body = body_obj
        else:
            body = 'No content'
        
        return f"**{title}**\n\n{body}"
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.search_pages(query, max_results)
        pages = result.get("results", []) if isinstance(result, dict) else []
        
        if not pages:
            return "No pages found."
        
        lines = [f"**Search Results** ({len(pages)}):\n"]
        for r in pages:
            if not isinstance(r, dict):
                continue
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            lines.append(f"- **{title}**: {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


async def confluence_get_page_by_url(url: str) -> str:
    """Get a Confluence page by its URL.
    
    Uses the URL to find the correct Confluence instance automatically.
    
    Args:
        url: Full Confluence page URL (e.g., https://company.atlassian.net/wiki/spaces/SPACE/pages/123456789/Page-Title)
    """
    # Get the correct instance client based on URL
    instance_channel = confluence_channel.get_instance_client(url=url)
    
    try:
        if not instance_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        
        # Extract page ID from URL
        import re
        
        page_id = None
        
        # Format 1: /spaces/KEY/pages/ID/title
        match = re.search(r'/pages/(\d+)/', url)
        if match:
            page_id = match.group(1)
        
        # Format 2: ?pageId=ID
        if not page_id:
            match = re.search(r'[?&]pageId=(\d+)', url)
            if match:
                page_id = match.group(1)
        
        # Format 3: /pages/ID (no title)
        if not page_id:
            match = re.search(r'/pages/(\d+)(?:\?|$)', url)
            if match:
                page_id = match.group(1)
        
        if not page_id:
            return f"Could not extract page ID from URL: {url}"
        
        # Use instance channel to get page
        page = await instance_channel.get_page(page_id)
        
        # Handle case where page might not be a dict
        if not isinstance(page, dict):
            return f"Error: Invalid page response - expected dict, got {type(page)}"
        
        title = page.get('title', 'Untitled')
        
        # Handle body - could be string or dict
        body_obj = page.get('body', {})
        if isinstance(body_obj, dict):
            body = body_obj.get('storage', {}).get('value', 'No content')
        elif isinstance(body_obj, str):
            body = body_obj
        else:
            body = 'No content'
        
        return f"**{title}**\n\nURL: {url}\n\n{body}"
    except Exception as e:
        return f"Error getting page by URL: {e}"


async def confluence_create_page(space_key: str, title: str, body: str = "", parent_id: str = None) -> str:
    """Create a new Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.create_page(space_key, title, body, parent_id)
        
        if isinstance(result, dict):
            page_id = result.get("id", "unknown")
            url = f"{confluence_channel.base_url}/pages/viewpage.action?pageId={page_id}"
            return f"Page created: **{title}**\nID: {page_id}\nURL: {url}"
        return str(result)
    except Exception as e:
        return f"Error creating page: {e}"


async def confluence_update_page(page_id: str, title: str = None, body: str = None) -> str:
    """Update an existing Confluence page."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        result = await confluence_channel.update_page(page_id, title, body)
        return f"Page {page_id} updated successfully"
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
                "description": "Get a Confluence page by its ID",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Confluence page ID (numeric)"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page_by_url",
                "description": "Get a Confluence page directly by its full URL. Use this when user provides a Confluence page URL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full Confluence page URL (e.g., https://company.atlassian.net/wiki/spaces/SPACE/pages/123456789/Page-Title)"}
                    },
                    "required": ["url"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Search Confluence pages using CQL (Confluence Query Language)",
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
                        "space_key": {"type": "string", "description": "Optional space key to limit search", "default": None}
                    },
                    "required": ["title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_create_page",
                "description": "Create a new Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space_key": {"type": "string", "description": "Space key (e.g., 'DEV', 'TEAM')"},
                        "title": {"type": "string", "description": "Page title"},
                        "body": {"type": "string", "description": "Page content in HTML format", "default": ""},
                        "parent_id": {"type": "string", "description": "Parent page ID for hierarchy", "default": None}
                    },
                    "required": ["space_key", "title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_update_page",
                "description": "Update an existing Confluence page",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Page ID to update"},
                        "title": {"type": "string", "description": "New title (optional)"},
                        "body": {"type": "string", "description": "New content in HTML format (optional)"}
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
                        "user_id": {"type": "string", "description": "User ID", "default": None},
                        "username": {"type": "string", "description": "Username", "default": None}
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
