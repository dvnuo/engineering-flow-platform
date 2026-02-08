"""
Confluence Tools - Agent entry point.

Calls src/integrations/confluence/api.py
"""

from src.integrations.confluence import ConfluenceChannel

# Global instance
confluence_client = ConfluenceChannel()

# ========== Tool Functions ==========

async def confluence_get_page(space: str, title: str) -> str:
    """Get a Confluence page by space and title."""
    try:
        page = await confluence_client.get_page(space, title)
        if not page:
            return f"No page found: {space}:{title}"
        body = page.get("body") or {}
        storage = body.get("storage") or {}
        content = (storage.get("value") or "")[:500]
        page_title = page.get("title") or "No title"
        return f"**{page_title}**\n\n{content}..."
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages using CQL."""
    try:
        result = await confluence_client.search(query, max_results)
        results = result.get("results", [])
        if not results:
            return "No pages found."
        lines = [f"**Search Results** ({len(results)}):\n"]
        for r in results:
            title = (r.get("title") or "No title")[:40]
            space_data = r.get("space")
            space = space_data.get("key", "UNKNOWN") if space_data else "UNKNOWN"
            lines.append(f"- **{space}**: {title}")
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
                "description": "Get a Confluence page by space and title",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space": {"type": "string", "description": "Space key"},
                        "title": {"type": "string", "description": "Page title"}
                    },
                    "required": ["space", "title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Search Confluence pages using CQL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "CQL query"},
                        "max_results": {"type": "integer", "description": "Max results", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
    ]
