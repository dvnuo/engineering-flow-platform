"""Confluence Integration - Single source of truth for Confluence operations."""

from .api import ConfluenceChannel, confluence_channel, confluence_get_page, confluence_search

__all__ = ["ConfluenceChannel", "confluence_channel", "confluence_get_page", "confluence_search"]


# ========== Tool Functions ==========

async def confluence_get_page(page_id: str) -> str:
    """Get a Confluence page by ID."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        page = await confluence_channel.get_page(page_id)
        return f"**{page.get('title', 'Untitled')}**\n\n{page.get('body', {}).get('storage', {}).get('value', 'No content')[:500]}..."
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages."""
    try:
        if not confluence_channel.is_configured():
            return "Confluence is not configured. Please check your settings."
        results = await confluence_channel.search_pages(query, max_results)
        if not results:
            return "No pages found."
        lines = [f"**Search Results** ({len(results)}):\n"]
        for r in results:
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            lines.append(f"- **{title}**: {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


def get_tools_schemas() -> list:
    """Return Confluence tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page",
                "description": "Get a Confluence page by ID",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "page_id": {"type": "string", "description": "Confluence page ID"}
                    },
                    "required": ["page_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Search Confluence pages",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer", "description": "Maximum results", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
    ]
